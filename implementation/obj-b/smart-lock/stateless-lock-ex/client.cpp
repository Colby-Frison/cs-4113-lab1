
#include <grpcpp/grpcpp.h>
#include "scanner.grpc.pb.h"
#include <iostream>
#include <memory>

int main() {
    auto channel = grpc::CreateChannel(
        "localhost:50051",
        grpc::InsecureChannelCredentials()
    );

    auto stub = scanner::Scanner::NewStub(channel);

    scanner::ScanRequest request;
    request.set_source("sprintf(buffer, input);");

    scanner::ScanResponse response;
    grpc::ClientContext context;

    grpc::Status status = stub->Scan(
        &context, request, &response
    );

    if (status.ok()) {
        std::cout << "Vulnerable: "
                  << response.vulnerable() << "\n";
        std::cout << "Finding: "
                  << response.finding() << "\n";
    }

    return status.ok() ? 0 : 1;
}
