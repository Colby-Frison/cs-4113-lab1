// queue_server — C++ port of implementation/obj-a/queue/queue.py.
//
// One process, one bounded buffer, optional file and gRPC edges mixed together.
//
// Lifecycle (same order as the Python queue):
//   1. Start the metric-q-size sidecar.
//   2. Open every --outfile (a FIFO blocks here until the reader exists).
//   3. Start file consumers and gRPC sender threads (they pull from one buffer).
//   4. Bind --ingrpc listeners (admit into that same buffer).
//   5. Start --infile producers.
//   6. Join producers; if a listen port is open, stay up until Ctrl-C.
//   7. Push one stop marker; every consumer requeues it and exits.

#include "buffer.hpp"
#include "events.hpp"
#include "grpc_edge.hpp"
#include "job.hpp"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

// Brief sleep so other threads (especially the size sampler) can run.
// This is not service time — the queue itself does not sleep job waits.
constexpr double kYieldSec = 0.001;
constexpr double kSampleIntervalSec = 0.1;

struct Options {
    std::vector<std::string> infiles;   // -i / --infile (repeatable)
    std::vector<std::string> outfiles;  // -o / --outfile (repeatable)
    std::vector<std::string> ingrpc;    // --ingrpc host:port,workers
    std::vector<std::string> outgrpc;   // --outgrpc host:port,workers[,role]
    std::string qid = "q0";             // written into every Lab 0 event
    std::optional<int> qsize;           // omit => unbounded buffer
    std::string logfile;                // empty => stderr
};

void usage() {
    std::cerr
        << "queue_server\n"
        << "  [-i INFILE ...] [-o OUTFILE ...]\n"
        << "  [--ingrpc interface:port,workers ...]\n"
        << "  [--outgrpc host:port,workers[,server|queue] ...]\n"
        << "  [--qsize N] [--qid ID] [--logfile PATH]\n";
}

// Read argv[*index + 1] into *value and advance *index. False if missing.
bool take_value(int argc, char** argv, int* index, std::string* value) {
    if (*index + 1 >= argc) {
        return false;
    }
    *value = argv[++(*index)];
    return true;
}

Options parse_args(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string flag = argv[i];
        std::string value;
        if (flag == "--help" || flag == "-h") {
            usage();
            std::exit(0);
        } else if (flag == "-i" || flag == "--infile") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--infile needs a value");
            }
            options.infiles.push_back(value);
        } else if (flag == "-o" || flag == "--outfile") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--outfile needs a value");
            }
            options.outfiles.push_back(value);
        } else if (flag == "--ingrpc") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--ingrpc needs a value");
            }
            options.ingrpc.push_back(value);
        } else if (flag == "--outgrpc") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--outgrpc needs a value");
            }
            options.outgrpc.push_back(value);
        } else if (flag == "--qid") {
            if (!take_value(argc, argv, &i, &options.qid)) {
                throw std::runtime_error("--qid needs a value");
            }
        } else if (flag == "--qsize") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--qsize needs a value");
            }
            options.qsize = std::stoi(value);
        } else if (flag == "--logfile") {
            if (!take_value(argc, argv, &i, &options.logfile)) {
                throw std::runtime_error("--logfile needs a value");
            }
        } else {
            throw std::runtime_error("unknown flag " + flag);
        }
    }
    return options;
}

void sleep_yield() {
    std::this_thread::sleep_for(std::chrono::duration<double>(kYieldSec));
}

// Sidecar: log buffer occupancy so compute-use / plots can see queueing.
void worker_sidecar(JobBuffer& buffer, EventLog& log, std::atomic<bool>& stop) {
    while (!stop.load()) {
        log.info("metric-q-size " + std::to_string(buffer.size()));
        std::this_thread::sleep_for(std::chrono::duration<double>(kSampleIntervalSec));
    }
}

// Producer: read one CSV or FIFO end-to-end.
// On a full buffer, admit_job logs QUEUE-FULL and drops — never blocks.
void worker_producer(
    JobBuffer& buffer,
    EventLog& log,
    int identifier,
    const std::string& filename,
    const std::string& qid) {
    log.info("Producer " + std::to_string(identifier) + " running");
    std::ifstream input(filename);
    if (!input) {
        log.error("cannot open infile " + filename);
        return;
    }
    std::string line;
    while (std::getline(input, line)) {
        const auto parsed = parse_job_line(line);
        if (parsed.status == ParseOutcome::Status::skip) {
            continue;  // blank or header
        }
        if (parsed.status == ParseOutcome::Status::error) {
            log.error(
                "skipping unreadable job line (" + parsed.error + "): " + line.substr(0, 200));
            continue;
        }
        admit_job(buffer, parsed.job, qid, filename, log);
        sleep_yield();
    }
    log.info("Producer " + std::to_string(identifier) + " done");
}

// File consumer: write the original CSV row and log DEPART-VIA.
// take() returning nullopt means shutdown — the stop marker was requeued.
void worker_consumer(
    JobBuffer& buffer,
    EventLog& log,
    int identifier,
    std::ofstream* handle,
    const std::string& outfile,
    const std::string& qid) {
    log.info("Consumer " + std::to_string(identifier) + " running");
    while (true) {
        auto item = buffer.take();
        if (!item.has_value()) {
            break;
        }
        (*handle) << item->raw_line << '\n';
        handle->flush();  // so a FIFO reader sees the row promptly
        log.info(EventLog::depart_via(item->job_id, qid, outfile));
        sleep_yield();
    }
    log.info("Consumer " + std::to_string(identifier) + " done");
}

// gRPC sender: Submit to a peer. DEPART-VIA for accepted and rejected;
// skip DEPART-VIA only when the RPC itself failed.
void worker_grpc_sender(
    JobBuffer& buffer,
    EventLog& log,
    GrpcHub& hub,
    const DialTarget& dial,
    const std::string& qid) {
    log.info("gRPC sender " + dial.address + " running");
    while (true) {
        auto item = buffer.take();
        if (!item.has_value()) {
            break;
        }
        if (hub.submit(dial, *item, qid) != SubmitOutcome::failed) {
            log.info(EventLog::depart_via(item->job_id, qid, dial.address));
        }
        sleep_yield();
    }
    log.info("gRPC sender " + dial.address + " done");
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2) {
            usage();
            return 2;
        }
        const Options options = parse_args(argc, argv);

        // --- validate flags -------------------------------------------------
        if (options.qsize.has_value() && *options.qsize < 1) {
            throw std::runtime_error("--qsize must be >= 1");
        }
        if (options.qid.empty()) {
            throw std::runtime_error("--qid must be non-empty");
        }
        if (options.infiles.empty() && options.ingrpc.empty()) {
            throw std::runtime_error("need at least one --infile or --ingrpc");
        }
        if (options.outfiles.empty() && options.outgrpc.empty()) {
            throw std::runtime_error("need at least one --outfile or --outgrpc");
        }

        // --- logging + buffer -----------------------------------------------
        EventLog log(options.logfile, "Q");
        log.info("Started");
        log.info("qid " + options.qid);

        std::optional<std::size_t> max_size;
        if (options.qsize.has_value()) {
            max_size = static_cast<std::size_t>(*options.qsize);
            log.info("max-q-size " + std::to_string(*options.qsize));
        }
        JobBuffer buffer(max_size);

        // --- sidecar --------------------------------------------------------
        std::atomic<bool> stop_sidecar {false};
        std::thread sidecar(worker_sidecar, std::ref(buffer), std::ref(log), std::ref(stop_sidecar));

        // --- open file outputs BEFORE producers -----------------------------
        // On POSIX a FIFO open(O_WRONLY) blocks until a reader exists, so we
        // cannot admit jobs while the next stage is still starting.
        std::vector<std::unique_ptr<std::ofstream>> handles;
        for (const auto& path : options.outfiles) {
            auto out = std::make_unique<std::ofstream>(path, std::ios::binary | std::ios::app);
            if (!*out) {
                throw std::runtime_error("cannot open " + path);
            }
            handles.push_back(std::move(out));
        }

        // --- parse dial targets ---------------------------------------------
        GrpcHub hub(log);
        std::vector<DialTarget> dials;
        for (const auto& text : options.outgrpc) {
            DialTarget dial;
            std::string error;
            if (!parse_dial_target(text, &dial, &error)) {
                throw std::runtime_error(error);
            }
            dials.push_back(dial);
        }

        // --- start consumers (file + gRPC senders) --------------------------
        // All of them call take() on the same buffer; whichever wins delivers.
        std::vector<std::thread> consumers;
        for (size_t i = 0; i < handles.size(); ++i) {
            consumers.emplace_back(
                worker_consumer,
                std::ref(buffer),
                std::ref(log),
                static_cast<int>(i),
                handles[i].get(),
                options.outfiles[i],
                options.qid);
        }
        for (const auto& dial : dials) {
            // workers on --outgrpc = number of sender threads for this peer.
            for (int n = 0; n < dial.workers; ++n) {
                consumers.emplace_back(
                    worker_grpc_sender,
                    std::ref(buffer),
                    std::ref(log),
                    std::ref(hub),
                    dial,
                    options.qid);
            }
        }

        // --- start gRPC listeners -------------------------------------------
        // Same admit_job path as file producers (identical QUEUE-FULL rule).
        auto admit = [&](const WaitJob& job, const std::string& source) {
            return admit_job(buffer, job, options.qid, source, log);
        };
        for (const auto& text : options.ingrpc) {
            ListenTarget listen;
            std::string error;
            if (!parse_listen_target(text, &listen, &error)) {
                throw std::runtime_error(error);
            }
            hub.start_listen(listen, admit);
        }

        // --- start file producers -------------------------------------------
        std::vector<std::thread> producers;
        for (size_t i = 0; i < options.infiles.size(); ++i) {
            producers.emplace_back(
                worker_producer,
                std::ref(buffer),
                std::ref(log),
                static_cast<int>(i),
                options.infiles[i],
                options.qid);
        }
        for (auto& producer : producers) {
            producer.join();
        }

        // --- wait for Ctrl-C if listening, then shut down -------------------
        // No-op when there were no --ingrpc ports.
        hub.serve_until_interrupt();

        buffer.push_stop();
        for (auto& consumer : consumers) {
            consumer.join();
        }

        stop_sidecar.store(true);
        sidecar.join();
        log.info("Finished");
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << exc.what() << "\n";
        return 1;
    }
}
