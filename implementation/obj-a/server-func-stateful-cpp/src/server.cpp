#include "job.hpp"
#include "queue_to_server.grpc.pb.h"
#include "runtime.hpp"
#include "server_to_queue.grpc.pb.h"
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <mutex>
#include <utility>

struct Options {
    std::string input_path;
    std::string listen_endpoint;
    std::string output_path;
    std::string output_address;
    std::string log_path;
    std::string queue_id = "q0";
    int server_id = 0;
};

class StatefulServer final : public QueueToServer::Service {
  public:
    StatefulServer(const Options &options, EventLog &log)
        : options_(options), log_(log),
          max_workers_(options.listen_endpoint.empty()
                           ? 1
                           : parse_endpoint(options.listen_endpoint, true).workers) {
        if (!options.output_path.empty()) {
            output_file_.open(options.output_path, std::ios::app);
            if (!output_file_)
                throw std::runtime_error("cannot open output: " + options.output_path);
        }
        if (!options.output_address.empty()) {
            queue_client_ = ServerToQueue::NewStub(
                grpc::CreateChannel(options.output_address, grpc::InsecureChannelCredentials()));
        }
    }

    grpc::Status Submit(grpc::ServerContext *context, const SubmitJobRequest *request,
                        SubmitJobResponse *response) override {
        WaitJob job;
        auto status = decode_job(request->job(), job);
        if (!status.ok())
            return status;

        // bound concurrent visits without serializing their task waits behind the KV lock
        std::unique_lock<std::mutex> lock(workers_mutex_);
        while (active_visits_ >= max_workers_ && !shutdown_requested && !context->IsCancelled()) {
            worker_available_.wait_for(lock, std::chrono::milliseconds(100));
        }
        if (shutdown_requested || context->IsCancelled())
            return {grpc::StatusCode::CANCELLED, "visit cancelled"};
        ++active_visits_;
        lock.unlock(); // other visits can run while this one sleeps or sends RPCs
        try {
            serve(job);
            response->set_accepted(true);
        } catch (const std::exception &error) {
            log_.error(error.what());
            status = {grpc::StatusCode::INTERNAL, error.what()};
        }
        // give the worker back even when the visit failed
        lock.lock();
        --active_visits_;
        lock.unlock();
        worker_available_.notify_one();
        return status;
    }

    void serve(const WaitJob &job) {
        log_.info(EventLog::enters(job.job_id, options_.queue_id, options_.server_id));
        count_visit(job.job_id);
        for (const auto &task : job.tasks) {
            log_.info(
                EventLog::task_start(job.job_id, options_.queue_id, task.tid, task.wait_seconds));
            std::this_thread::sleep_for(std::chrono::duration<double>(task.wait_seconds));
            log_.info(
                EventLog::task_end(job.job_id, options_.queue_id, task.tid, task.wait_seconds));
        }
        log_.info(EventLog::exits(job.job_id, options_.queue_id, options_.server_id));
        forward(job);
    }

    // called after all visits finish so the final counts need no lock
    void finish() {
        for (const auto &[key, count] : visit_counts_)
            log_.info("state-visits " + key + " " + std::to_string(count));
    }

  private:
    // accept raw CSV from existing queues or rebuild it from protobuf fields
    static grpc::Status decode_job(const Job &message, WaitJob &job) {
        auto row = message.raw_line();
        if (row.empty()) {
            if (!std::isfinite(message.arrival_time()) || message.arrival_time() < 0) {
                return {grpc::StatusCode::INVALID_ARGUMENT,
                        "arrival time must be finite and nonnegative"};
            }
            WaitJob structured_job;
            structured_job.job_id = message.job_id();
            structured_job.arrival_time = message.arrival_time();
            for (const auto &task : message.tasks()) {
                if (task.tid() < 0 || !std::isfinite(task.wait_seconds()) ||
                    task.wait_seconds() < 0) {
                    return {grpc::StatusCode::INVALID_ARGUMENT,
                            "task IDs and waits must be nonnegative and finite"};
                }
                structured_job.tasks.push_back({task.tid(), task.wait_seconds()});
            }
            row = format_job_line(structured_job);
        }
        auto parsed = parse_job_line(row);
        if (parsed.status != ParseOutcome::Status::ok) {
            return {grpc::StatusCode::INVALID_ARGUMENT, "invalid job: " + parsed.error};
        }

        job = std::move(parsed.job);
        return grpc::Status::OK;
    }

    void count_visit(const std::string &job_id) {
        // each visit increments one key, even when its job ID was seen before
        std::lock_guard<std::mutex> lock(state_mutex_);
        auto &count = visit_counts_[job_id];
        if (count == std::numeric_limits<std::uint64_t>::max())
            throw std::runtime_error("visit count overflow");
        ++count;
    }

    void forward(const WaitJob &job) {
        if (!options_.output_path.empty()) {
            // keep concurrent visits from mixing their CSV rows
            std::lock_guard<std::mutex> lock(output_mutex_);
            output_file_ << job.raw_line << '\n';
            output_file_.flush();
            if (!output_file_)
                throw std::runtime_error("output write failed: " + options_.output_path);
            log_.info(EventLog::depart_via(job.job_id, options_.queue_id,
                                           EventLog::edge_label(options_.output_path)));
            return;
        }
        if (!queue_client_) {
            log_.info(EventLog::no_out_edge(job.job_id, options_.queue_id));
            return;
        }
        // include both formats so Python and C++ queues can read the same job
        SubmitJobRequest request;
        request.set_source_edge(options_.queue_id);
        auto *outgoing_job = request.mutable_job();
        outgoing_job->set_job_id(job.job_id);
        outgoing_job->set_arrival_time(job.arrival_time);
        outgoing_job->set_raw_line(job.raw_line);
        for (const auto &task : job.tasks) {
            auto *outgoing_task = outgoing_job->add_tasks();
            outgoing_task->set_tid(task.tid);
            outgoing_task->set_wait_seconds(task.wait_seconds);
        }
        grpc::ClientContext context;
        context.set_deadline(rpc_deadline(30));
        SubmitJobResponse response;
        auto status = queue_client_->Submit(&context, request, &response);
        if (!status.ok()) {
            log_.error("gRPC forward failed: " + status.error_message());
            return;
        }
        // a full downstream queue owns the drop log; this visit has still departed
        log_.info(EventLog::depart_via(job.job_id, options_.queue_id, options_.output_address));
    }

    const Options &options_;
    EventLog &log_;
    std::map<std::string, std::uint64_t> visit_counts_;
    std::mutex state_mutex_;   // protects the in-memory visit counts
    std::mutex output_mutex_;  // protects the shared file stream
    std::mutex workers_mutex_; // protects the active visit count
    std::condition_variable worker_available_;
    int max_workers_;
    int active_visits_ = 0;
    std::ofstream output_file_;
    std::unique_ptr<ServerToQueue::Stub> queue_client_;
};

int main(int argc, char **argv) {
    try {
        Options options;
        for (int i = 1; i < argc; ++i) {
            std::string flag = argv[i];
            if (flag == "--help" || flag == "-h") {
                std::cout << "stateful_server (-i FILE | --ingrpc host:port,workers)\n"
                             "  [-o FILE | --outgrpc host:port,workers]\n"
                             "  [--qid q0] [--server-id 0] [--logfile FILE]\n";
                return 0;
            }
            if (++i == argc)
                throw std::runtime_error("missing value for " + flag);
            std::string value = argv[i];
            if (flag == "-i" || flag == "--infile")
                options.input_path = value;
            else if (flag == "--ingrpc")
                options.listen_endpoint = value;
            else if (flag == "-o" || flag == "--outfile" || flag == "--outgrpc") {
                if (!options.output_path.empty() || !options.output_address.empty())
                    throw std::runtime_error("choose at most one output: -o or --outgrpc");
                if (flag == "--outgrpc")
                    options.output_address = parse_endpoint(value).address;
                else
                    options.output_path = value;
            } else if (flag == "--qid")
                options.queue_id = value;
            else if (flag == "--server-id")
                options.server_id = parse_integer(value, 0);
            else if (flag == "--logfile")
                options.log_path = value;
            else
                throw std::runtime_error("unknown option: " + flag);
        }
        if (options.input_path.empty() == options.listen_endpoint.empty())
            throw std::runtime_error("choose -i or --ingrpc");
        EventLog log(options.log_path, "sv");
        StatefulServer service(options, log);
        if (!options.listen_endpoint.empty()) {
            run_service(service, parse_endpoint(options.listen_endpoint, true), log);
        } else {
            std::ifstream input(options.input_path);
            if (!input)
                throw std::runtime_error("cannot read input: " + options.input_path);
            std::string row;
            while (std::getline(input, row)) {
                auto parsed = parse_job_line(row);
                if (parsed.status == ParseOutcome::Status::ok)
                    service.serve(parsed.job);
                else if (parsed.status == ParseOutcome::Status::error)
                    log.error(parsed.error);
            }
        }
        service.finish();
    } catch (const std::exception &error) {
        std::cerr << "stateful_server: " << error.what() << '\n';
        return 1;
    }
}
