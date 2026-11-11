// one serial food-delivery resource, exposed through Objective A's gRPC API
#include "common.hpp"

class FoodServer final : public QueueToServer::Service {
    std::string stage_, qid_, worker_, destination_;
    std::set<std::string> restaurants_;
    Logger log_;
    std::ofstream sink_;
    std::unique_ptr<ServerToQueue::Stub> downstream_;
    std::mutex resource_;
    std::set<std::string> seen_;

    bool execute_task(grpc::ServerContext *ctx, const Job &job, int tid) {
        const double wait = job.tasks(tid).wait_seconds();
        const std::string detail = std::to_string(tid) + " \"W " + number(wait) + "\"";
        log_.event(job, qid_, "TASK-START", detail);
        const auto start = Steady::now();
        // short sleeps let cancellation stop a long simulated task promptly
        while (std::chrono::duration<double>(Steady::now() - start).count() < wait) {
            if (ctx->IsCancelled())
                return false;
            const double remaining =
                wait - std::chrono::duration<double>(Steady::now() - start).count();
            if (remaining > 0)
                std::this_thread::sleep_for(
                    std::chrono::duration<double>(std::min(remaining, 0.01)));
        }
        if (ctx->IsCancelled())
            return false;
        log_.event(job, qid_, "TASK-END", detail);
        return true;
    }

  public:
    FoodServer(const std::string &stage, const std::string &qid, const std::string &worker,
               const std::string &log, const std::string &out, const std::string &peer,
               const std::set<std::string> &restaurants)
        : stage_(stage), qid_(qid), worker_(worker), destination_(peer.empty() ? out : peer),
          restaurants_(restaurants), log_(log) {
        if (!peer.empty())
            downstream_ = ServerToQueue::NewStub(
                grpc::CreateChannel(peer, grpc::InsecureChannelCredentials()));
        else {
            sink_.open(out);
            sink_.exceptions(std::ios::failbit | std::ios::badbit);
        }
    }
    grpc::Status Submit(grpc::ServerContext *ctx, const SubmitJobRequest *request,
                        SubmitJobResponse *response) override {
        // one process represents one worker, held through service and downstream output
        std::unique_lock<std::mutex> guard(resource_, std::try_to_lock);
        if (!guard.owns_lock())
            return {grpc::StatusCode::RESOURCE_EXHAUSTED, "worker already busy"};
        try {
            if (!request->has_job())
                throw std::invalid_argument("missing job");
            Job job = validate_request(request->job(), restaurants_);
            if (ctx->IsCancelled())
                return {grpc::StatusCode::CANCELLED, "request cancelled"};
            if (!seen_.insert(job.job_id()).second)
                return {grpc::StatusCode::ALREADY_EXISTS,
                        "job already submitted; automatic retries are not supported"};
            log_.event(job, qid_, "ENTERS-SERVER", worker_);
            if (stage_ != "kitchen")
                log_.message("courier-assigned " + worker_ + " " + job.job_id());
            if (stage_ != "courier" && !execute_task(ctx, job, 0))
                return {grpc::StatusCode::CANCELLED, "preparation cancelled"};
            if (stage_ != "kitchen" && !execute_task(ctx, job, 1))
                return {grpc::StatusCode::CANCELLED, "delivery cancelled"};
            log_.event(job, qid_, "EXITS-SERVER", worker_);
            if (downstream_) {
                grpc::ClientContext outbound;
                outbound.set_deadline(
                    std::min(ctx->deadline(), Clock::now() + std::chrono::seconds(30)));
                SubmitJobRequest next;
                *next.mutable_job() = job;
                next.set_source_edge(qid_);
                SubmitJobResponse admitted;
                auto status = downstream_->Submit(&outbound, next, &admitted);
                if (!status.ok())
                    return {grpc::StatusCode::UNAVAILABLE,
                            "downstream handoff failed: " + status.error_message()};
                // accepted=false is a logged queue drop, not a transport failure
            } else {
                if (ctx->IsCancelled())
                    return {grpc::StatusCode::CANCELLED, "completion cancelled"};
                sink_ << job.raw_line() << '\n';
                sink_.flush();
            }
            log_.event(job, qid_, "DEPART-VIA", destination_);
            if (stage_ != "kitchen")
                log_.message("courier-released " + worker_ + " " + job.job_id());
            response->set_accepted(true);
            return grpc::Status::OK;
        } catch (const std::invalid_argument &e) {
            return {grpc::StatusCode::INVALID_ARGUMENT, e.what()};
        } catch (const std::exception &e) {
            return {grpc::StatusCode::INTERNAL, e.what()};
        }
    }
};

int main(int argc, char **argv) {
    try {
        std::map<std::string, std::string> args;
        const std::set<std::string> allowed{"--stage",   "--qid",        "--server-id",
                                            "--logfile", "--out",        "--outgrpc",
                                            "--listen",  "--ready-file", "--restaurants"};
        for (int i = 1; i < argc; i += 2) {
            if (i + 1 == argc || !allowed.count(argv[i]) ||
                !args.emplace(argv[i], argv[i + 1]).second)
                throw std::invalid_argument("expected unique --option value pairs");
        }
        for (const auto &key : {"--stage", "--qid", "--server-id", "--logfile", "--ready-file"})
            if (args[key].empty())
                throw std::invalid_argument(std::string("required: ") + key);
        const auto stage = args["--stage"];
        if (stage != "monolith" && stage != "kitchen" && stage != "courier")
            throw std::invalid_argument("stage must be monolith, kitchen, or courier");
        if (args["--out"].empty() == args["--outgrpc"].empty())
            throw std::invalid_argument("provide exactly one of --out or --outgrpc");
        if (!std::regex_match(args["--server-id"], std::regex("[0-9]+")) ||
            !std::regex_match(args["--qid"], std::regex("q[0-9]+")))
            throw std::invalid_argument("invalid queue or worker ID");
        std::string restaurant_text = args.count("--restaurants") ? args["--restaurants"] : "0";
        if (!std::regex_match(restaurant_text, std::regex("[0-9]+(,[0-9]+)*")))
            throw std::invalid_argument("restaurants must be comma-separated nonnegative IDs");
        std::set<std::string> restaurants;
        std::stringstream restaurant_stream(restaurant_text);
        std::string restaurant;
        while (std::getline(restaurant_stream, restaurant, ','))
            restaurants.insert(restaurant);
        FoodServer service(stage, args["--qid"], args["--server-id"], args["--logfile"],
                           args["--out"], args["--outgrpc"], restaurants);
        grpc::ServerBuilder builder;
        int port = 0;
        builder.AddListeningPort(args.count("--listen") ? args["--listen"] : "127.0.0.1:0",
                                 grpc::InsecureServerCredentials(), &port);
        builder.RegisterService(&service);
        auto server = builder.BuildAndStart();
        if (!server || !port)
            throw std::runtime_error("could not bind gRPC listener");
        std::ofstream ready(args["--ready-file"]);
        ready.exceptions(std::ios::failbit | std::ios::badbit);
        ready << port << '\n';
        ready.close();
        server->Wait();
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
