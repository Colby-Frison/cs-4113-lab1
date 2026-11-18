
#include <grpcpp/grpcpp.h>
#include "scanner.grpc.pb.h"
#include <iostream>
#include <memory>
#include <string>

class ScannerService final : public scanner::Scanner::Service {
public:
    grpc::Status Scan(
        grpc::ServerContext* context,
        const scanner::ScanRequest* request,
        scanner::ScanResponse* response) override {

        std::string source = request->source();

        bool vulnerable =
            source.find("sprintf(") != std::string::npos;

        response->set_vulnerable(vulnerable);
        response->set_finding(
            vulnerable ? "Unsafe sprintf detected" : "No finding"
        );

        return grpc::Status::OK;
    }
};

int main() {
    ScannerService service;
    grpc::ServerBuilder builder;

    builder.AddListeningPort(
        "0.0.0.0:50051",
        grpc::InsecureServerCredentials()
    );

    builder.RegisterService(&service);

    std::unique_ptr<grpc::Server> server = builder.BuildAndStart();

    std::cout << "Scanner running on port 50051\n";
    server->Wait();
}
