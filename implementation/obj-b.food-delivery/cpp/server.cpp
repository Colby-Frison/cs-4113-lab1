// One serial food-delivery resource, exposed through Objective A's gRPC API.
#include <grpcpp/grpcpp.h>
#include <google/protobuf/struct.pb.h>
#include <google/protobuf/util/json_util.h>
#include "queue_to_server.grpc.pb.h"
#include "server_to_queue.grpc.pb.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::system_clock;
using Steady = std::chrono::steady_clock;

std::string number(double value) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(3) << value;
    return out.str();
}

// Parse RFC-style CSV quoting, including doubled quotes in the Tasks JSON.
std::vector<std::string> csv_fields(const std::string& row) {
    if (row.find_first_of("\r\n") != std::string::npos)
        throw std::invalid_argument("job row must be a single line");
    std::vector<std::string> fields;
    size_t pos = 0;
    while (true) {
        std::string field;
        if (pos < row.size() && row[pos] == '"') {
            ++pos;
            bool closed = false;
            while (pos < row.size()) {
                char c = row[pos++];
                if (c != '"') field += c;
                else if (pos < row.size() && row[pos] == '"') { field += '"'; ++pos; }
                else { closed = true; break; }
            }
            if (!closed) throw std::invalid_argument("unclosed CSV quote");
        } else {
            while (pos < row.size() && row[pos] != ',') {
                if (row[pos] == '"') throw std::invalid_argument("unexpected CSV quote");
                field += row[pos++];
            }
        }
        fields.push_back(field);
        if (pos == row.size()) break;
        if (row[pos++] != ',') throw std::invalid_argument("expected CSV separator");
    }
    return fields;
}

double seconds(const std::string& text) {
    size_t used = 0;
    double value;
    try { value = std::stod(text, &used); }
    catch (const std::exception&) { throw std::invalid_argument("invalid duration/time"); }
    if (used != text.size() || !std::isfinite(value) || value < 0)
        throw std::invalid_argument("time must be finite and nonnegative");
    return value;
}

Job parse_row(const std::string& row) {
    const auto fields = csv_fields(row);
    if (fields.size() != 3) throw std::invalid_argument("expected three CSV fields");
    Job job;
    job.set_job_id(fields[0]);
    job.set_arrival_time(seconds(fields[1]));
    job.set_raw_line(row);
    google::protobuf::ListValue tasks;
    if (!google::protobuf::util::JsonStringToMessage(fields[2], &tasks).ok())
        throw std::invalid_argument("invalid task JSON list");
    static const std::regex task_pattern(R"(^([01]):\s*W\s*([0-9]+(?:\.[0-9]+)?)$)");
    for (const auto& value : tasks.values()) {
        std::smatch match;
        const auto text = value.string_value();
        if (value.kind_case() != google::protobuf::Value::kStringValue ||
            !std::regex_match(text, match, task_pattern))
            throw std::invalid_argument("invalid wait task");
        auto* task = job.add_tasks();
        task->set_tid(std::stoi(match[1]));
        task->set_wait_seconds(seconds(match[2]));
    }
    return job;
}

Job validate_request(const Job& message, const std::set<std::string>& restaurants) {
    Job job = message;
    if (!message.raw_line().empty()) {
        job = parse_row(message.raw_line());
        // A caller may send only raw_line, or the complete shared Job message.
        if (!message.job_id().empty() || message.tasks_size() != 0) {
            if (message.job_id() != job.job_id() || message.arrival_time() != job.arrival_time() ||
                message.tasks_size() != job.tasks_size())
                throw std::invalid_argument("structured job disagrees with CSV");
            for (int i = 0; i < job.tasks_size(); ++i)
                if (message.tasks(i).tid() != job.tasks(i).tid() ||
                    message.tasks(i).wait_seconds() != job.tasks(i).wait_seconds())
                    throw std::invalid_argument("structured tasks disagree with CSV");
        }
    }
    static const std::regex order_pattern(R"(^o[0-9]+\.r([0-9]+)\.(near|mid|far)\.i[1-9][0-9]*$)");
    std::smatch match;
    const std::string id = job.job_id();
    if (!std::regex_match(id, match, order_pattern) || !restaurants.count(match[1].str()))
        throw std::invalid_argument("invalid food order ID or unsupported restaurant");
    if (!std::isfinite(job.arrival_time()) || job.arrival_time() < 0 || job.tasks_size() != 2)
        throw std::invalid_argument("invalid arrival time or task count");
    for (int i = 0; i < 2; ++i) {
        double wait = job.tasks(i).wait_seconds();
        if (job.tasks(i).tid() != i || !std::isfinite(wait) || wait < 0)
            throw std::invalid_argument("expected nonnegative preparation task 0 then delivery task 1");
    }
    if (job.raw_line().empty()) {
        // Canonical food-app precision for structured-only clients.
        std::string row = job.job_id() + "," + number(job.arrival_time()) + ",\"[\"\"0: W " +
            number(job.tasks(0).wait_seconds()) + "\"\", \"\"1: W " +
            number(job.tasks(1).wait_seconds()) + "\"\"]\"";
        job = parse_row(row);
    }
    return job;
}

class Logger {
    std::ofstream file_;
public:
    explicit Logger(const std::string& path) : file_(path) {
        file_.exceptions(std::ios::failbit | std::ios::badbit);
    }
    void message(const std::string& text) {
        auto now = Clock::now();
        auto stamp = Clock::to_time_t(now);
        std::tm local{};
        localtime_r(&stamp, &local);
        auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() % 1000;
        file_ << std::put_time(&local, "%Y-%m-%d %H:%M:%S") << ',' << std::setfill('0')
              << std::setw(3) << millis << " - sv - INFO - " << text << '\n';
        file_.flush();
    }
    void event(const Job& job, const std::string& qid, const std::string& kind, const std::string& detail) {
        const double now = std::chrono::duration<double>(Clock::now().time_since_epoch()).count();
        message(number(now) + " " + job.job_id() + " IN " + qid + " " + kind + " " + detail);
    }
};

class FoodServer final : public QueueToServer::Service {
    std::string stage_, qid_, worker_, destination_;
    std::set<std::string> restaurants_;
    Logger log_;
    std::ofstream sink_;
    std::unique_ptr<ServerToQueue::Stub> downstream_;
    std::mutex resource_;
    std::set<std::string> seen_;

    bool execute_task(grpc::ServerContext* ctx, const Job& job, int tid) {
        const double wait = job.tasks(tid).wait_seconds();
        const std::string detail = std::to_string(tid) + " \"W " + number(wait) + "\"";
        log_.event(job, qid_, "TASK-START", detail);
        const auto start = Steady::now();
        while (std::chrono::duration<double>(Steady::now() - start).count() < wait) {
            if (ctx->IsCancelled()) return false;
            const double remaining = wait - std::chrono::duration<double>(Steady::now() - start).count();
            if (remaining > 0) std::this_thread::sleep_for(std::chrono::duration<double>(std::min(remaining, 0.01)));
        }
        if (ctx->IsCancelled()) return false;
        log_.event(job, qid_, "TASK-END", detail);
        return true;
    }
public:
    FoodServer(const std::string& stage, const std::string& qid, const std::string& worker,
               const std::string& log, const std::string& out, const std::string& peer, const std::set<std::string>& restaurants)
        : stage_(stage), qid_(qid), worker_(worker), destination_(peer.empty() ? out : peer), restaurants_(restaurants), log_(log) {
        if (!peer.empty()) downstream_ = ServerToQueue::NewStub(grpc::CreateChannel(peer, grpc::InsecureChannelCredentials()));
        else {
            sink_.open(out);
            sink_.exceptions(std::ios::failbit | std::ios::badbit);
        }
    }
    grpc::Status Submit(grpc::ServerContext* ctx, const SubmitJobRequest* request, SubmitJobResponse* response) override {
        std::unique_lock<std::mutex> guard(resource_, std::try_to_lock);
        if (!guard.owns_lock()) return {grpc::StatusCode::RESOURCE_EXHAUSTED, "worker already busy"};
        try {
            if (!request->has_job()) throw std::invalid_argument("missing job");
            Job job = validate_request(request->job(), restaurants_);
            if (ctx->IsCancelled()) return {grpc::StatusCode::CANCELLED, "request cancelled"};
            if (!seen_.insert(job.job_id()).second)
                return {grpc::StatusCode::ALREADY_EXISTS, "job already submitted; automatic retries are not supported"};
            log_.event(job, qid_, "ENTERS-SERVER", worker_);
            if (stage_ != "kitchen") log_.message("courier-assigned " + worker_ + " " + job.job_id());
            if (stage_ != "courier" && !execute_task(ctx, job, 0))
                return {grpc::StatusCode::CANCELLED, "preparation cancelled"};
            if (stage_ != "kitchen" && !execute_task(ctx, job, 1))
                return {grpc::StatusCode::CANCELLED, "delivery cancelled"};
            log_.event(job, qid_, "EXITS-SERVER", worker_);
            if (downstream_) {
                grpc::ClientContext outbound;
                outbound.set_deadline(std::min(ctx->deadline(), Clock::now() + std::chrono::seconds(30)));
                SubmitJobRequest next;
                *next.mutable_job() = job;
                next.set_source_edge(qid_);
                SubmitJobResponse admitted;
                auto status = downstream_->Submit(&outbound, next, &admitted);
                if (!status.ok()) return {grpc::StatusCode::UNAVAILABLE, "downstream handoff failed: " + status.error_message()};
                // accepted=false is a logged queue drop, not a transport failure.
            } else {
                if (ctx->IsCancelled()) return {grpc::StatusCode::CANCELLED, "completion cancelled"};
                sink_ << job.raw_line() << '\n';
                sink_.flush();
            }
            log_.event(job, qid_, "DEPART-VIA", destination_);
            if (stage_ != "kitchen") log_.message("courier-released " + worker_ + " " + job.job_id());
            response->set_accepted(true);
            return grpc::Status::OK;
        } catch (const std::invalid_argument& e) {
            return {grpc::StatusCode::INVALID_ARGUMENT, e.what()};
        } catch (const std::exception& e) {
            return {grpc::StatusCode::INTERNAL, e.what()};
        }
    }
};

int main(int argc, char** argv) {
    try {
        std::map<std::string, std::string> args;
        const std::set<std::string> allowed{"--stage", "--qid", "--server-id", "--logfile", "--out", "--outgrpc", "--listen", "--ready-file", "--restaurants"};
        for (int i = 1; i < argc; i += 2) {
            if (i + 1 == argc || !allowed.count(argv[i]) || !args.emplace(argv[i], argv[i + 1]).second)
                throw std::invalid_argument("expected unique --option value pairs");
        }
        for (const auto& key : {"--stage", "--qid", "--server-id", "--logfile", "--ready-file"})
            if (args[key].empty()) throw std::invalid_argument(std::string("required: ") + key);
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
        while (std::getline(restaurant_stream, restaurant, ',')) restaurants.insert(restaurant);
        FoodServer service(stage, args["--qid"], args["--server-id"], args["--logfile"], args["--out"], args["--outgrpc"], restaurants);
        grpc::ServerBuilder builder;
        int port = 0;
        builder.AddListeningPort(args.count("--listen") ? args["--listen"] : "127.0.0.1:0",
                                 grpc::InsecureServerCredentials(), &port);
        builder.RegisterService(&service);
        auto server = builder.BuildAndStart();
        if (!server || !port) throw std::runtime_error("could not bind gRPC listener");
        std::ofstream ready(args["--ready-file"]);
        ready.exceptions(std::ios::failbit | std::ios::badbit);
        ready << port << '\n';
        ready.close();
        server->Wait();
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
