#include "grpc_edge.hpp"

#include <grpcpp/grpcpp.h>

#include <chrono>
#include <csignal>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <thread>

#include "queue_to_server.grpc.pb.h"
#include "server_to_queue.grpc.pb.h"

namespace {

volatile std::sig_atomic_t g_stop = 0;

void on_signal(int) { g_stop = 1; }

bool split_host_port(const std::string& address, std::string* host, int* port, std::string* error) {
    const auto colon = address.rfind(':');
    if (colon == std::string::npos || colon == 0 || colon + 1 >= address.size()) {
        *error = "address needs host:port";
        return false;
    }
    *host = address.substr(0, colon);
    try {
        *port = std::stoi(address.substr(colon + 1));
    } catch (const std::exception&) {
        *error = "port is not a number";
        return false;
    }
    if (*port < 1 || *port > 65535) {
        *error = "port is out of range";
        return false;
    }
    return true;
}

bool split_address_workers(
    const std::string& text, std::string* address, int* workers, std::string* error) {
    const auto comma = text.find(',');
    if (comma == std::string::npos || text.find(',', comma + 1) != std::string::npos) {
        *error = "expected host:port,workers";
        return false;
    }
    *address = text.substr(0, comma);
    try {
        *workers = std::stoi(text.substr(comma + 1));
    } catch (const std::exception&) {
        *error = "workers is not a number";
        return false;
    }
    if (*workers < 1) {
        *error = "workers must be >= 1";
        return false;
    }
    return true;
}

void fill_job(Job* message, const TicketJob& job) {
    message->set_job_id(job.job_id);
    message->set_arrival_time(job.arrival_time);
    message->set_raw_line(job.raw_line);
    for (const auto& task : job.tasks) {
        auto* out = message->add_tasks();
        out->set_tid(task.tid);
        out->set_wait_seconds(task.wait_seconds);
    }
}

TicketJob job_from_message(const Job& message, std::string* error) {
    if (!message.raw_line().empty()) {
        auto parsed = parse_job_line(message.raw_line());
        if (parsed.status == ParseOutcome::Status::ok) {
            return parsed.job;
        }
    }
    TicketJob job;
    job.job_id = message.job_id();
    job.arrival_time = message.arrival_time();
    for (const auto& task : message.tasks()) {
        job.tasks.push_back(TaskWait {task.tid(), task.wait_seconds()});
    }
    if (job.job_id.empty() || job.tasks.empty()) {
        *error = "gRPC job has no tasks and no parseable raw_line";
        return {};
    }
    job.raw_line = format_job_line(job);
    return job;
}

class VisitService final : public QueueToServer::Service {
public:
    VisitService(EventLog& log, const std::function<void(const TicketJob&)>& visit)
        : log_(log), visit_(visit) {}

    grpc::Status Submit(
        grpc::ServerContext*,
        const SubmitJobRequest* request,
        SubmitJobResponse* response) override {
        std::string error;
        TicketJob job = job_from_message(request->job(), &error);
        if (!error.empty()) {
            log_.error("skipping unreadable gRPC job: " + error);
            response->set_accepted(true);
            return grpc::Status::OK;
        }
        visit_(job);
        response->set_accepted(true);
        return grpc::Status::OK;
    }

private:
    EventLog& log_;
    std::function<void(const TicketJob&)> visit_;
};

}  // namespace

bool parse_listen_target(const std::string& text, ListenTarget* out, std::string* error) {
    std::string address;
    if (!split_address_workers(text, &address, &out->workers, error)) {
        return false;
    }
    if (!split_host_port(address, &out->host, &out->port, error)) {
        return false;
    }
    out->address = out->host + ":" + std::to_string(out->port);
    return true;
}

bool parse_dial_target(const std::string& text, DialTarget* out, std::string* error) {
    std::string address;
    if (!split_address_workers(text, &address, &out->workers, error)) {
        return false;
    }
    if (!split_host_port(address, &out->host, &out->port, error)) {
        return false;
    }
    out->address = out->host + ":" + std::to_string(out->port);
    return true;
}

GrpcHub::GrpcHub(EventLog& log) : log_(log) {}

bool GrpcHub::submit(const DialTarget& dial, const TicketJob& job, const std::string& source_edge) {
    auto channel = grpc::CreateChannel(dial.address, grpc::InsecureChannelCredentials());
    auto stub = ServerToQueue::NewStub(channel);
    grpc::ClientContext context;
    SubmitJobRequest request;
    request.set_source_edge(source_edge);
    fill_job(request.mutable_job(), job);
    SubmitJobResponse response;
    const auto status = stub->Submit(&context, request, &response);
    if (!status.ok()) {
        log_.error("gRPC submit to " + dial.address + " failed: " + status.error_message());
        return false;
    }
    return true;
}

void GrpcHub::serve_until_interrupt(
    const ListenTarget& listen, const std::function<void(const TicketJob&)>& visit) {
    VisitService service(log_, visit);
    grpc::ServerBuilder builder;
    grpc::ResourceQuota quota;
    quota.SetMaxThreads(listen.workers);
    builder.SetResourceQuota(quota);
    int bound = 0;
    builder.AddListeningPort(listen.address, grpc::InsecureServerCredentials(), &bound);
    builder.RegisterService(&service);
    std::unique_ptr<grpc::Server> server(builder.BuildAndStart());
    if (!server || bound == 0) {
        throw std::runtime_error("cannot listen on " + listen.address);
    }
    log_.info(
        "gRPC listen " + listen.host + ":" + std::to_string(bound) + " workers " +
        std::to_string(listen.workers) + "; Ctrl-C to stop");
    g_stop = 0;
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);
    while (g_stop == 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
    log_.info("stopping gRPC listen");
    server->Shutdown();
}
