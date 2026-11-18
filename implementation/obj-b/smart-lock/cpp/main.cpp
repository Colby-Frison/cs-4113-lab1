#include "server_function.hpp"
#include "smart_lock.grpc.pb.h"

#include <grpcpp/grpcpp.h>
#include <iostream>
#include <memory>

class ScannerService final : public smartlock::Scanner::Service {
    grpc::Status Execute(
        grpc::ServerContext*,
        const smartlock::Request* request,
        smartlock::Reply* reply
    ) override {
        try {
            const std::string& stage = request->stage();

            if (stage == "Prepare")
                reply->set_prepared(clean_source(request->source()));
            else if (stage == "Scan" || stage == "Monolithic") {
                auto source = stage == "Monolithic"
                    ? clean_source(request->source())
                    : request->source();

                for (const auto& finding : find_candidates(source, request->check())) {
                    auto* out = reply->add_findings();
                    out->set_rule(finding.rule);
                    out->set_line(finding.line);
                    out->set_symbol(finding.symbol);
                    out->set_reason(finding.reason);
                }

                if (stage == "Monolithic")
                    reply->set_status(reply->findings_size() ? "candidate" : "no_candidate");
            } else if (stage == "Review" || stage == "Report") {
                *reply->mutable_findings() = request->findings();
                reply->set_status(
                    stage == "Review"
                        ? (request->findings_size() ? "candidate" : "no_candidate")
                        : request->status()
                );
            } else
                return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "unknown stage");

            return grpc::Status::OK;
        } catch (const std::exception& e) {
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, e.what());
        }
    }
};

int main(int argc, char** argv) {
    std::string address = argc > 1 ? argv[1] : "127.0.0.1:50051";

    ScannerService service;
    grpc::ServerBuilder builder;

    int port = 0;
    builder.AddListeningPort(address, grpc::InsecureServerCredentials(), &port);
    builder.SetMaxReceiveMessageSize(32 * 1024 * 1024);
    builder.SetMaxSendMessageSize(32 * 1024 * 1024);
    builder.RegisterService(&service);

    auto server = builder.BuildAndStart();
    if (!server || !port) {
        std::cerr << "could not bind gRPC server\n";
        return 1;
    }

    std::cout << "Smart-lock C++ server listening on " << address << std::endl;
    server->Wait();
}