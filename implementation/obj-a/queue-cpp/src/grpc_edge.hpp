#pragma once

#include "events.hpp"
#include "job.hpp"

#include <functional>
#include <memory>
#include <string>
#include <vector>

// Flag parsing and gRPC wiring for the Objective A queue edges.
// Protos: ServerToQueue / QueueToQueue on listen; QueueToServer (default) or
// QueueToQueue (",queue") on dial. gRPC types stay in grpc_edge.cpp (pimpl).

enum class DialRole { server, queue };

// --outgrpc host:port,workers[,server|queue]
struct DialTarget {
    std::string host;
    int port = 0;
    int workers = 1;  // sender thread count in main, not a gRPC pool
    DialRole role = DialRole::server;
    std::string address;  // "host:port"
};

// --ingrpc interface:port,workers
struct ListenTarget {
    std::string host;
    int port = 0;
    int workers = 1;  // gRPC server thread quota
    std::string address;
};

// accepted / rejected = RPC returned; failed = transport error (no DEPART-VIA).
enum class SubmitOutcome { accepted, rejected, failed };

bool parse_listen_target(const std::string& text, ListenTarget* out, std::string* error);
bool parse_dial_target(const std::string& text, DialTarget* out, std::string* error);

class GrpcHub {
public:
    explicit GrpcHub(EventLog& log);
    ~GrpcHub();

    SubmitOutcome submit(const DialTarget& dial, const WaitJob& job, const std::string& source_edge);

    // Bind one listen port; both ingress services share admit.
    void start_listen(
        const ListenTarget& listen,
        const std::function<bool(const WaitJob&, const std::string&)>& admit);

    // Block until Ctrl-C when at least one listen was started; otherwise return.
    void serve_until_interrupt();

private:
    struct Impl;
    EventLog& log_;
    std::unique_ptr<Impl> impl_;
};
