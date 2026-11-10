// One asynchronous gRPC event loop hosts many independently limited queues.
#include "common.hpp"
#include <grpcpp/alarm.h>
#include <memory>

struct Queue {
    std::string id, stage, destination;
    std::set<std::string> restaurants, seen;
    std::vector<bool> busy;
    Logger log;
    std::ofstream sink;
    Queue(std::string q, std::string s, int workers, std::set<std::string> allowed,
          const std::string& logfile, const std::string& output)
        : id(std::move(q)), stage(std::move(s)), destination(output),
          restaurants(std::move(allowed)), busy(workers, false), log(logfile) {
        if (stage == "courier") {
            sink.open(output);
            sink.exceptions(std::ios::failbit | std::ios::badbit);
        }
    }
};

class Host {
    QueueToServer::AsyncService service_;
    std::unique_ptr<grpc::ServerCompletionQueue> cq_;
    std::unique_ptr<grpc::Server> server_;
    std::map<std::string, std::unique_ptr<Queue>> queues_;

    class Call {
        Host& host_;
        grpc::ServerContext context_;
        SubmitJobRequest request_;
        SubmitJobResponse response_;
        grpc::ServerAsyncResponseWriter<SubmitJobResponse> responder_{&context_};
        grpc::Alarm timer_;
        enum Phase { REQUEST, TIMER, FINISH } phase_ = REQUEST;
        Queue* queue_ = nullptr;
        int worker_ = -1, tid_ = -1;
        bool reserved_ = false;
        Job job_;

        void finish(const grpc::Status& status) {
            if (reserved_) { queue_->busy[worker_] = false; reserved_ = false; }
            phase_ = FINISH;
            responder_.Finish(response_, status, this);
        }
        std::string task() const {
            return std::to_string(tid_) + " \"W " + number(job_.tasks(tid_).wait_seconds()) + "\"";
        }
    public:
        explicit Call(Host& host) : host_(host) {
            host_.service_.RequestSubmit(&context_, &request_, &responder_,
                                        host_.cq_.get(), host_.cq_.get(), this);
        }
        void proceed(bool ok) {
            if (phase_ == FINISH || !ok) {
                if (reserved_) queue_->busy[worker_] = false;
                delete this;
                return;
            }
            try {
                if (phase_ == REQUEST) {
                    new Call(host_); // Always keep an accept pending.
                    auto found = host_.queues_.find(request_.source_edge());
                    if (found == host_.queues_.end()) throw std::invalid_argument("unknown queue source_edge");
                    queue_ = found->second.get();
                    auto metadata = context_.client_metadata().find("worker-id");
                    if (metadata == context_.client_metadata().end()) throw std::invalid_argument("missing worker-id metadata");
                    std::string worker(metadata->second.data(), metadata->second.size());
                    if (!std::regex_match(worker, std::regex("[0-9]{1,6}"))) throw std::invalid_argument("invalid worker-id");
                    worker_ = std::stoi(worker);
                    if (worker_ >= static_cast<int>(queue_->busy.size())) throw std::invalid_argument("worker-id outside queue pool");
                    if (!request_.has_job()) throw std::invalid_argument("missing job");
                    job_ = validate_request(request_.job(), queue_->restaurants);
                    if (Clock::now() >= context_.deadline()) {
                        finish({grpc::StatusCode::DEADLINE_EXCEEDED, "expired before service"}); return;
                    }
                    if (queue_->seen.count(job_.job_id())) {
                        finish({grpc::StatusCode::ALREADY_EXISTS, "job already submitted; retries unsupported"}); return;
                    }
                    if (queue_->busy[worker_]) {
                        finish({grpc::StatusCode::RESOURCE_EXHAUSTED, "worker already busy"}); return;
                    }
                    queue_->seen.insert(job_.job_id());
                    queue_->busy[worker_] = true; reserved_ = true;
                    queue_->log.event(job_, queue_->id, "ENTERS-SERVER", worker);
                    tid_ = queue_->stage == "kitchen" ? 0 : 1;
                    queue_->log.event(job_, queue_->id, "TASK-START", task());
                    auto end = Clock::now() + std::chrono::duration_cast<Clock::duration>(
                        std::chrono::duration<double>(job_.tasks(tid_).wait_seconds()));
                    phase_ = TIMER;
                    timer_.Set(host_.cq_.get(), std::min(end, context_.deadline()), this);
                } else {
                    if (Clock::now() >= context_.deadline()) {
                        finish({grpc::StatusCode::DEADLINE_EXCEEDED, "expired during service"}); return;
                    }
                    queue_->log.event(job_, queue_->id, "TASK-END", task());
                    queue_->log.event(job_, queue_->id, "EXITS-SERVER", std::to_string(worker_));
                    if (queue_->stage == "courier") {
                        queue_->sink << job_.raw_line() << '\n'; queue_->sink.flush();
                    }
                    queue_->log.event(job_, queue_->id, "DEPART-VIA", queue_->destination);
                    response_.set_accepted(true);
                    finish(grpc::Status::OK);
                }
            } catch (const std::invalid_argument& e) {
                finish({grpc::StatusCode::INVALID_ARGUMENT, e.what()});
            } catch (const std::exception& e) {
                finish({grpc::StatusCode::INTERNAL, e.what()});
            }
        }
    };
public:
    explicit Host(const std::string& path) {
        std::ifstream input(path);
        if (!input) throw std::runtime_error("cannot read host configuration");
        std::string text((std::istreambuf_iterator<char>(input)), {});
        google::protobuf::Struct config;
        if (!google::protobuf::util::JsonStringToMessage(text, &config).ok()) throw std::invalid_argument("invalid JSON configuration");
        const auto& entries = config.fields().at("queues");
        if (entries.kind_case() != google::protobuf::Value::kListValue) throw std::invalid_argument("queues must be a list");
        for (const auto& value : entries.list_value().values()) {
            const auto& f = value.struct_value().fields();
            auto string = [&](const char* key) {
                const auto& v = f.at(key);
                if (v.kind_case() != google::protobuf::Value::kStringValue || v.string_value().empty())
                    throw std::invalid_argument(std::string("invalid ") + key);
                return v.string_value();
            };
            auto integer = [](const google::protobuf::Value& v) {
                double n = v.number_value();
                if (v.kind_case() != google::protobuf::Value::kNumberValue || !std::isfinite(n) || n < 0 || n > 100000 || std::floor(n) != n)
                    throw std::invalid_argument("invalid nonnegative integer");
                return static_cast<int>(n);
            };
            std::string id = string("qid"), stage = string("stage");
            int workers = integer(f.at("workers"));
            if (!workers || !std::regex_match(id, std::regex("q[0-9]+")) ||
                (stage != "kitchen" && stage != "courier") || queues_.count(id)) throw std::invalid_argument("invalid queue configuration");
            const auto& list = f.at("restaurants");
            if (list.kind_case() != google::protobuf::Value::kListValue) throw std::invalid_argument("restaurants must be a list");
            std::set<std::string> restaurants;
            for (const auto& r : list.list_value().values()) restaurants.insert(std::to_string(integer(r)));
            if (restaurants.empty()) throw std::invalid_argument("empty restaurant allowlist");
            queues_[id] = std::make_unique<Queue>(id, stage, workers, restaurants, string("logfile"), string("destination"));
        }
        if (queues_.empty()) throw std::invalid_argument("empty host");
    }
    void run(const std::string& listen, const std::string& ready_path) {
        grpc::ServerBuilder builder;
        int port = 0;
        builder.AddListeningPort(listen, grpc::InsecureServerCredentials(), &port);
        builder.RegisterService(&service_);
        cq_ = builder.AddCompletionQueue();
        server_ = builder.BuildAndStart();
        if (!server_ || !port) throw std::runtime_error("could not bind hosted listener");
        std::ofstream ready(ready_path);
        ready.exceptions(std::ios::failbit | std::ios::badbit); ready << port << '\n'; ready.close();
        new Call(*this);
        void* tag; bool ok;
        // Only this thread mutates queues, worker reservations, logs, and sinks.
        while (cq_->Next(&tag, &ok)) static_cast<Call*>(tag)->proceed(ok);
    }
};

int main(int argc, char** argv) {
    try {
        std::map<std::string, std::string> args;
        for (int i = 1; i < argc; i += 2) {
            std::string key = argv[i];
            if (i + 1 == argc || (key != "--config" && key != "--ready-file" && key != "--listen") || !args.emplace(key, argv[i + 1]).second)
                throw std::invalid_argument("expected unique --config/--ready-file/--listen pairs");
        }
        if (args["--config"].empty() || args["--ready-file"].empty()) throw std::invalid_argument("config and ready-file are required");
        Host host(args["--config"]);
        host.run(args.count("--listen") ? args["--listen"] : "127.0.0.1:0", args["--ready-file"]);
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
