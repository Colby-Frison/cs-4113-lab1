// C++ port of the router server functions. One binary. --stage selects the visit:
// monolith (the default), validate, lookup, or forward. The flags, input files, RPCs,
// log lines, and decisions match server-monolith and server-stages in Python.
//
// Submit returns only after the visit, including the hand-on, so a queue's sender
// thread stays busy for the whole visit. gRPC's sync server sizes its own thread
// pool, so a counting gate is what limits visits to the server count.

#include <grpcpp/grpcpp.h>
#include <signal.h>
#include <sys/time.h>
#include <unistd.h>

#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <ctime>
#include <exception>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <utility>

#include "job.pb.h"
#include "queue_to_server.grpc.pb.h"
#include "routing.hpp"
#include "server_to_queue.grpc.pb.h"

namespace {

using packets::kLocal;
using packets::QueueAddress;
using packets::RoutingTable;

enum class Stage { kMonolith, kValidate, kLookup, kForward };

constexpr int kValidateTid = 0;
constexpr int kLookupTid = 1;
constexpr int kForwardTid = 2;
constexpr int kAllTasks = -1;

struct Options {
    Stage stage = Stage::kMonolith;
    std::string qid;
    int server_id = 0;
    std::string listen_address;
    int workers = 0;
    std::string addresses;
    std::string routes;
    std::string sink;
    std::string next_qid;
    std::string logfile;
};

// Writes the same prefix as lib/logsetup.py: asctime - sv_pid@host - LEVEL - message.
class Logger {
public:
    explicit Logger(const std::string& path) {
        if (!path.empty()) {
            file_.open(path, std::ios::app);
            if (!file_) {
                throw std::runtime_error("cannot open log file " + path);
            }
        }
        char host[256] = "localhost";
        gethostname(host, sizeof(host) - 1);
        component_ = "sv_pid" + std::to_string(getpid()) + "@" + host;
    }

    void Info(const std::string& message) { Write("INFO", message); }
    void Error(const std::string& message) { Write("ERROR", message); }

private:
    void Write(const char* level, const std::string& message) {
        timeval now{};
        gettimeofday(&now, nullptr);
        tm local{};
        localtime_r(&now.tv_sec, &local);
        char stamp[32];
        strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &local);
        char millis[8];
        snprintf(millis, sizeof(millis), ",%03d", static_cast<int>(now.tv_usec / 1000));

        const std::lock_guard<std::mutex> guard(mutex_);
        std::ostream& out = file_.is_open() ? static_cast<std::ostream&>(file_) : std::cerr;
        out << stamp << millis << " - " << component_ << " - " << level << " - " << message << "\n";
        out.flush();
    }

    std::mutex mutex_;
    std::ofstream file_;
    std::string component_;
};

std::string Event(const std::string& job_id, const std::string& qid, const std::string& kind,
                  const std::string& detail = "") {
    const double now = std::chrono::duration<double>(
                           std::chrono::system_clock::now().time_since_epoch())
                           .count();
    char stamp[32];
    snprintf(stamp, sizeof(stamp), "%.3f", now);
    std::string line = std::string(stamp) + " " + job_id + " IN " + qid + " " + kind;
    if (!detail.empty()) {
        line += " " + detail;
    }
    return line;
}

std::string TaskDetail(int tid, double seconds) {
    char wait[64];
    snprintf(wait, sizeof(wait), "%d \"W %.6f\"", tid, seconds);
    return wait;
}

// Allows at most the server count of visits at once.
class Gate {
public:
    explicit Gate(int slots) : slots_(slots) {}
    void Enter() {
        std::unique_lock<std::mutex> lock(mutex_);
        ready_.wait(lock, [this] { return slots_ > 0; });
        --slots_;
    }
    void Leave() {
        {
            const std::lock_guard<std::mutex> guard(mutex_);
            ++slots_;
        }
        ready_.notify_one();
    }

private:
    std::mutex mutex_;
    std::condition_variable ready_;
    int slots_;
};

class GateSlot {
public:
    explicit GateSlot(Gate& gate) : gate_(gate) { gate_.Enter(); }
    ~GateSlot() { gate_.Leave(); }

private:
    Gate& gate_;
};

class Router final : public QueueToServer::Service {
public:
    Router(Options options, Logger& log)
        : options_(std::move(options)),
          log_(log),
          addresses_(packets::LoadAddresses(options_.addresses)),
          gate_(options_.workers) {
        if (!options_.routes.empty()) {
            table_ = RoutingTable::Load(options_.routes);
        }
        if (!options_.sink.empty()) {
            sink_.open(options_.sink, std::ios::app);
            if (!sink_) {
                throw std::runtime_error("cannot open sink " + options_.sink);
            }
            const auto slash = options_.sink.find_last_of('/');
            sink_label_ = slash == std::string::npos ? options_.sink : options_.sink.substr(slash + 1);
        }
    }

    grpc::Status Submit(grpc::ServerContext*, const SubmitJobRequest* request,
                        SubmitJobResponse* response) override {
        const GateSlot slot(gate_);
        const Job& job = request->job();
        switch (options_.stage) {
            case Stage::kMonolith:
                Monolith(job);
                break;
            case Stage::kValidate:
                Validate(job);
                break;
            case Stage::kLookup:
                Lookup(job);
                break;
            case Stage::kForward:
                Work(job, kForwardTid);
                HandOn(job, job.next_hop());
                break;
        }
        response->set_accepted(true);
        return grpc::Status::OK;
    }

private:
    void Monolith(const Job& job) {
        Work(job, kAllTasks);
        if (!TtlIsValid(job)) {
            return;
        }
        const auto next_hop = LookupNextHop(job);
        if (next_hop) {
            HandOn(job, *next_hop);
        }
    }

    void Validate(const Job& job) {
        Work(job, kValidateTid);
        if (TtlIsValid(job)) {
            Send(job, options_.next_qid);
        }
    }

    void Lookup(const Job& job) {
        Work(job, kLookupTid);
        const auto next_hop = LookupNextHop(job);
        if (next_hop) {
            Job updated = job;
            updated.set_next_hop(*next_hop);
            Send(updated, options_.next_qid);
        }
    }

    void Work(const Job& job, int tid) {
        const std::string server = std::to_string(options_.server_id);
        log_.Info(Event(job.job_id(), options_.qid, "ENTERS-SERVER", server));
        for (const auto& task : job.tasks()) {
            if (tid != kAllTasks && task.tid() != tid) {
                continue;
            }
            const std::string detail = TaskDetail(task.tid(), task.wait_seconds());
            log_.Info(Event(job.job_id(), options_.qid, "TASK-START", detail));
            std::this_thread::sleep_for(std::chrono::duration<double>(task.wait_seconds()));
            log_.Info(Event(job.job_id(), options_.qid, "TASK-END", detail));
        }
        log_.Info(Event(job.job_id(), options_.qid, "EXITS-SERVER", server));
    }

    bool TtlIsValid(const Job& job) {
        if (job.ttl() > 0) {
            return true;
        }
        log_.Info(Event(job.job_id(), options_.qid, "ERROR", "TTL-EXPIRED"));
        return false;
    }

    std::optional<std::string> LookupNextHop(const Job& job) {
        auto next_hop = table_.Lookup(job.dst_ip());
        if (!next_hop) {
            log_.Info(Event(job.job_id(), options_.qid, "ERROR", "NO-OUT-EDGE"));
        }
        return next_hop;
    }

    // Delivers here, or reduces the TTL and sends to the next router.
    void HandOn(const Job& job, const std::string& next_hop) {
        if (next_hop == kLocal) {
            {
                const std::lock_guard<std::mutex> guard(sink_mutex_);
                sink_ << job.raw_line() << "\n";
                sink_.flush();
            }
            log_.Info(Event(job.job_id(), options_.qid, "DEPART-VIA", sink_label_));
            return;
        }
        const int ttl = job.ttl() - 1;
        if (ttl <= 0) {
            log_.Info(Event(job.job_id(), options_.qid, "ERROR", "TTL-EXPIRED"));
            return;
        }
        Job updated = job;
        updated.set_ttl(ttl);
        updated.clear_next_hop();
        Send(updated, next_hop);
    }

    void Send(const Job& job, const std::string& dest_qid) {
        const auto found = addresses_.find(dest_qid);
        if (found == addresses_.end()) {
            log_.Error("no address for " + dest_qid);
            log_.Info(Event(job.job_id(), options_.qid, "ERROR", "NO-OUT-EDGE"));
            return;
        }
        const std::string address =
            found->second.host + ":" + std::to_string(found->second.queue_port);

        SubmitJobRequest request;
        *request.mutable_job() = job;
        request.set_source_edge(options_.qid);
        SubmitJobResponse response;
        grpc::ClientContext context;
        const grpc::Status status = StubFor(address).Submit(&context, request, &response);
        if (!status.ok()) {
            // The job did not leave, so there is no DEPART-VIA.
            log_.Error("gRPC submit to " + address + " failed: " + status.error_message());
            return;
        }
        // accepted=false means the next queue logged QUEUE-FULL. The job still left here.
        log_.Info(Event(job.job_id(), options_.qid, "DEPART-VIA", address));
    }

    // One channel per next hop, opened once and shared by every worker.
    ServerToQueue::Stub& StubFor(const std::string& address) {
        const std::lock_guard<std::mutex> guard(stubs_mutex_);
        auto& stub = stubs_[address];
        if (!stub) {
            stub = ServerToQueue::NewStub(
                grpc::CreateChannel(address, grpc::InsecureChannelCredentials()));
        }
        return *stub;
    }

    Options options_;
    Logger& log_;
    std::map<std::string, QueueAddress> addresses_;
    Gate gate_;
    RoutingTable table_;
    std::mutex sink_mutex_;
    std::ofstream sink_;
    std::string sink_label_;
    std::mutex stubs_mutex_;
    std::map<std::string, std::unique_ptr<ServerToQueue::Stub>> stubs_;
};

[[noreturn]] void Usage(const std::string& problem) {
    std::cerr << "packet_server: " << problem << "\n"
              << "usage: packet_server [--stage monolith|validate|lookup|forward]\n"
              << "  --qid QID --ingrpc interface:port,workers --addresses PATH\n"
              << "  [--routes PATH] [--sink PATH] [--next-qid QID]\n"
              << "  [--server-id N] [--logfile PATH]\n";
    std::exit(2);
}

Stage ParseStage(const std::string& text) {
    if (text == "monolith") return Stage::kMonolith;
    if (text == "validate") return Stage::kValidate;
    if (text == "lookup") return Stage::kLookup;
    if (text == "forward") return Stage::kForward;
    Usage("unknown stage " + text);
}

Options ParseOptions(int argc, char** argv) {
    Options options;
    std::string ingrpc;
    for (int index = 1; index < argc; index += 2) {
        const std::string flag = argv[index];
        if (index + 1 >= argc) {
            Usage(flag + " needs a value");
        }
        const std::string value = argv[index + 1];
        if (flag == "--stage") {
            options.stage = ParseStage(value);
        } else if (flag == "--qid") {
            options.qid = value;
        } else if (flag == "--server-id") {
            options.server_id = std::stoi(value);
        } else if (flag == "--ingrpc") {
            ingrpc = value;
        } else if (flag == "--addresses") {
            options.addresses = value;
        } else if (flag == "--routes") {
            options.routes = value;
        } else if (flag == "--sink") {
            options.sink = value;
        } else if (flag == "--next-qid") {
            options.next_qid = value;
        } else if (flag == "--logfile") {
            options.logfile = value;
        } else {
            Usage("unknown flag " + flag);
        }
    }

    const auto comma = ingrpc.find(',');
    if (comma == std::string::npos || ingrpc.find(':') == std::string::npos) {
        Usage("--ingrpc must be interface:port,workers");
    }
    options.listen_address = ingrpc.substr(0, comma);
    options.workers = std::stoi(ingrpc.substr(comma + 1));
    if (options.workers < 1) {
        Usage("--ingrpc workers must be at least 1");
    }
    if (options.qid.empty()) Usage("--qid is required");
    if (options.addresses.empty()) Usage("--addresses is required");

    const Stage stage = options.stage;
    if ((stage == Stage::kMonolith || stage == Stage::kLookup) && options.routes.empty()) {
        Usage("this stage needs --routes");
    }
    if ((stage == Stage::kMonolith || stage == Stage::kForward) && options.sink.empty()) {
        Usage("this stage needs --sink");
    }
    if ((stage == Stage::kValidate || stage == Stage::kLookup) && options.next_qid.empty()) {
        Usage("this stage needs --next-qid");
    }
    return options;
}

}  // namespace

int main(int argc, char** argv) {
    const Options options = ParseOptions(argc, argv);

    // Block the stop signals before any thread starts, so only sigwait below sees them.
    sigset_t stop_signals;
    sigemptyset(&stop_signals);
    sigaddset(&stop_signals, SIGINT);
    sigaddset(&stop_signals, SIGTERM);
    pthread_sigmask(SIG_BLOCK, &stop_signals, nullptr);

    try {
        Logger log(options.logfile);
        log.Info("Started");
        log.Info("qid " + options.qid + " server-id " + std::to_string(options.server_id));
        Router router(options, log);

        grpc::ServerBuilder builder;
        int port = 0;
        builder.AddListeningPort(options.listen_address, grpc::InsecureServerCredentials(), &port);
        builder.RegisterService(&router);
        const std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
        if (!server || port == 0) {
            log.Error("cannot bind gRPC address " + options.listen_address);
            return 1;
        }
        log.Info("gRPC listen " + options.listen_address + " workers " +
                 std::to_string(options.workers) + "; Ctrl-C to stop");

        int received = 0;
        sigwait(&stop_signals, &received);
        log.Info("stopping gRPC listen");
        server->Shutdown(std::chrono::system_clock::now() + std::chrono::seconds(1));
        server->Wait();
        log.Info("Finished");
    } catch (const std::exception& error) {
        std::cerr << "packet_server: " << error.what() << "\n";
        return 1;
    }
    return 0;
}
