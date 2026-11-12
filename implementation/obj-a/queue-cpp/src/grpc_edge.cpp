// gRPC listen/dial for the queue. Built only when CMake finds protobuf + gRPC.
//
// Ingress (--ingrpc): one port registers BOTH
//   - ServerToQueue  (server function -> this queue)
//   - QueueToQueue   (another queue -> this queue)
// Both call the same admit function / buffer. That matches Python
// lib/grpc_runtime._QueueAdmit.
//
// Egress (--outgrpc): dial role picks the stub
//   - server (default) -> QueueToServer
//   - queue            -> QueueToQueue

#include "grpc_edge.hpp"

#include <grpcpp/grpcpp.h>

#include <chrono>
#include <csignal>
#include <stdexcept>
#include <thread>

#include "queue_to_queue.grpc.pb.h"
#include "queue_to_server.grpc.pb.h"
#include "server_to_queue.grpc.pb.h"

namespace {

// Set by SIGINT/SIGTERM so serve_until_interrupt can leave its sleep loop.
volatile std::sig_atomic_t g_stop = 0;
void on_signal(int) { g_stop = 1; }

bool split_host_port(const std::string& address, std::string* host, int* port, std::string* error) {
    // Split on the last colon so IPv6-ish hosts with colons still work poorly
    // but host:port and 0.0.0.0:50051 parse correctly.
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
    if (*port < 0 || *port > 65535) {
        *error = "port is out of range";
        return false;
    }
    return true;
}

// Copy WaitJob into the protobuf Job message (structured fields + raw_line).
void fill_job(::Job* message, const WaitJob& job) {
    message->set_job_id(job.job_id);
    message->set_arrival_time(job.arrival_time);
    message->set_raw_line(job.raw_line);
    for (const auto& task : job.tasks) {
        auto* out = message->add_tasks();
        out->set_tid(task.tid);
        out->set_wait_seconds(task.wait_seconds);
    }
}

// Rebuild a WaitJob from an RPC. Prefer raw_line so file and gRPC edges agree.
WaitJob job_from_message(const ::Job& message, std::string* error) {
    if (!message.raw_line().empty()) {
        auto parsed = parse_job_line(message.raw_line());
        if (parsed.status == ParseOutcome::Status::ok) {
            return parsed.job;
        }
    }
    // Fallback: structured fields only (used if a peer omits raw_line).
    WaitJob job;
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

using AdmitFn = std::function<bool(const WaitJob&, const std::string&)>;

// Shared Submit handler for both ingress services.
// Response.accepted is whatever admit() returned (false => peer logged QUEUE-FULL).
grpc::Status handle_submit(
    EventLog& log,
    const AdmitFn& admit,
    const SubmitJobRequest* request,
    SubmitJobResponse* response) {
    std::string error;
    WaitJob job = job_from_message(request->job(), &error);
    if (!error.empty()) {
        // Bad payload: do not punish the sender with a transport error.
        log.error("skipping unreadable gRPC job: " + error);
        response->set_accepted(true);
        return grpc::Status::OK;
    }
    // source_edge is what ARRIVE-VIA prints (usually the caller's --qid).
    const std::string source = request->source_edge().empty() ? "grpc" : request->source_edge();
    response->set_accepted(admit(job, source));
    return grpc::Status::OK;
}

// gRPC codegen wants a separate Service subclass per .proto service.
class ServerToQueueAdmit final : public ServerToQueue::Service {
public:
    ServerToQueueAdmit(EventLog& log, AdmitFn admit) : log_(log), admit_(std::move(admit)) {}
    grpc::Status Submit(
        grpc::ServerContext*,
        const SubmitJobRequest* request,
        SubmitJobResponse* response) override {
        return handle_submit(log_, admit_, request, response);
    }

private:
    EventLog& log_;
    AdmitFn admit_;
};

class QueueToQueueAdmit final : public QueueToQueue::Service {
public:
    QueueToQueueAdmit(EventLog& log, AdmitFn admit) : log_(log), admit_(std::move(admit)) {}
    grpc::Status Submit(
        grpc::ServerContext*,
        const SubmitJobRequest* request,
        SubmitJobResponse* response) override {
        return handle_submit(log_, admit_, request, response);
    }

private:
    EventLog& log_;
    AdmitFn admit_;
};

// Owns the servicer objects for the lifetime of the grpc::Server.
// If the servicers were destroyed first, in-flight RPCs would crash.
struct OwnedServer {
    std::unique_ptr<ServerToQueueAdmit> server_to_queue;
    std::unique_ptr<QueueToQueueAdmit> queue_to_queue;
    std::unique_ptr<grpc::Server> server;
};

}  // namespace

// Pimpl storage so grpc headers stay out of grpc_edge.hpp.
struct GrpcHub::Impl {
    std::vector<std::unique_ptr<OwnedServer>> servers;
};

bool parse_listen_target(const std::string& text, ListenTarget* out, std::string* error) {
    // Form: interface:port,workers   e.g. 0.0.0.0:50051,4
    const auto comma = text.find(',');
    if (comma == std::string::npos || text.find(',', comma + 1) != std::string::npos) {
        *error = "expected host:port,workers";
        return false;
    }
    try {
        out->workers = std::stoi(text.substr(comma + 1));
    } catch (const std::exception&) {
        *error = "workers is not a number";
        return false;
    }
    if (out->workers < 1) {
        *error = "workers must be >= 1";
        return false;
    }
    if (!split_host_port(text.substr(0, comma), &out->host, &out->port, error)) {
        return false;
    }
    out->address = out->host + ":" + std::to_string(out->port);
    return true;
}

bool parse_dial_target(const std::string& text, DialTarget* out, std::string* error) {
    // Form: host:port,workers[,server|queue]
    std::vector<std::string> parts;
    std::string cur;
    for (char ch : text) {
        if (ch == ',') {
            parts.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(ch);
        }
    }
    parts.push_back(cur);

    if (parts.size() != 2 && parts.size() != 3) {
        *error = "expected host:port,workers or host:port,workers,role";
        return false;
    }
    if (!split_host_port(parts[0], &out->host, &out->port, error)) {
        return false;
    }
    try {
        out->workers = std::stoi(parts[1]);
    } catch (const std::exception&) {
        *error = "workers is not a number";
        return false;
    }
    if (out->workers < 1) {
        *error = "workers must be >= 1";
        return false;
    }

    // Default role is a server function. Pass ",queue" to call another queue.
    if (parts.size() == 2 || parts[2] == "server") {
        out->role = DialRole::server;
    } else if (parts[2] == "queue") {
        out->role = DialRole::queue;
    } else {
        *error = "queue --outgrpc role must be server or queue";
        return false;
    }
    out->address = out->host + ":" + std::to_string(out->port);
    return true;
}

GrpcHub::GrpcHub(EventLog& log) : log_(log), impl_(std::make_unique<Impl>()) {}

GrpcHub::~GrpcHub() = default;

SubmitOutcome GrpcHub::submit(
    const DialTarget& dial, const WaitJob& job, const std::string& source_edge) {
    // One channel per call (simple; matches matchmaking C++). Python caches
    // channels to avoid TIME_WAIT exhaustion on very long Windows runs.
    auto channel = grpc::CreateChannel(dial.address, grpc::InsecureChannelCredentials());
    grpc::ClientContext context;
    SubmitJobRequest request;
    request.set_source_edge(source_edge);
    fill_job(request.mutable_job(), job);
    SubmitJobResponse response;

    grpc::Status status;
    if (dial.role == DialRole::queue) {
        status = QueueToQueue::NewStub(channel)->Submit(&context, request, &response);
    } else {
        status = QueueToServer::NewStub(channel)->Submit(&context, request, &response);
    }

    if (!status.ok()) {
        log_.error("gRPC submit to " + dial.address + " failed: " + status.error_message());
        return SubmitOutcome::failed;  // caller must NOT log DEPART-VIA
    }
    // accepted=false means the peer's buffer was full; peer already logged QUEUE-FULL.
    return response.accepted() ? SubmitOutcome::accepted : SubmitOutcome::rejected;
}

void GrpcHub::start_listen(
    const ListenTarget& listen,
    const std::function<bool(const WaitJob&, const std::string&)>& admit) {
    auto owned = std::make_unique<OwnedServer>();
    owned->server_to_queue = std::make_unique<ServerToQueueAdmit>(log_, admit);
    owned->queue_to_queue = std::make_unique<QueueToQueueAdmit>(log_, admit);

    grpc::ServerBuilder builder;
    // workers on --ingrpc = max threads in the gRPC completion pool.
    grpc::ResourceQuota quota;
    quota.SetMaxThreads(listen.workers);
    builder.SetResourceQuota(quota);

    int bound = 0;
    builder.AddListeningPort(listen.address, grpc::InsecureServerCredentials(), &bound);
    builder.RegisterService(owned->server_to_queue.get());
    builder.RegisterService(owned->queue_to_queue.get());
    owned->server = builder.BuildAndStart();
    if (!owned->server || bound == 0) {
        throw std::runtime_error("cannot listen on " + listen.address);
    }

    log_.info(
        "gRPC listen " + listen.host + ":" + std::to_string(bound) + " workers " +
        std::to_string(listen.workers));
    impl_->servers.push_back(std::move(owned));
}

void GrpcHub::serve_until_interrupt() {
    // File-only runs never call start_listen, so this returns immediately.
    if (impl_->servers.empty()) {
        return;
    }
    log_.info("gRPC listen is running; Ctrl-C to stop");
    g_stop = 0;
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);
    while (g_stop == 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
    log_.info("stopping gRPC listen");
    for (auto& owned : impl_->servers) {
        // Give in-flight RPCs a short grace period before teardown.
        owned->server->Shutdown(std::chrono::system_clock::now() + std::chrono::seconds(1));
    }
    impl_->servers.clear();
}
