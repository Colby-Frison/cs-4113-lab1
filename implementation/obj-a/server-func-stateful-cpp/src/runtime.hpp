#pragma once

#include "events.hpp"
#include <chrono>
#include <csignal>
#include <grpcpp/grpcpp.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

inline std::vector<std::string> split(const std::string &text, char separator) {
    std::vector<std::string> parts;
    std::stringstream stream(text);
    std::string part;
    while (std::getline(stream, part, separator))
        parts.push_back(part);
    if (!text.empty() && text.back() == separator)
        parts.emplace_back();
    return parts;
}

inline int parse_integer(const std::string &text, int minimum) {
    std::size_t parsed_characters = 0;
    int value = std::stoi(text, &parsed_characters);
    if (parsed_characters != text.size() || value < minimum)
        throw std::runtime_error("invalid integer: " + text);
    return value;
}

struct Endpoint {
    std::string address;
    int workers;
};

inline void validate_address(const std::string &address, bool allow_zero = false) {
    auto colon = address.rfind(':');
    if (colon == std::string::npos || colon == 0)
        throw std::runtime_error("expected host:port: " + address);
    int port = parse_integer(address.substr(colon + 1), allow_zero ? 0 : 1);
    if (port > 65535)
        throw std::runtime_error("port exceeds 65535");
}

inline Endpoint parse_endpoint(const std::string &text, bool listen = false) {
    auto parts = split(text, ',');
    if (parts.size() != 2)
        throw std::runtime_error("expected host:port,workers: " + text);
    validate_address(parts[0], listen);
    return {parts[0], parse_integer(parts[1], 1)};
}

inline auto rpc_deadline(double seconds) {
    return std::chrono::system_clock::now() +
           std::chrono::duration_cast<std::chrono::system_clock::duration>(
               std::chrono::duration<double>(seconds));
}

// signal handlers only set a flag; the main loop handles shutdown
inline volatile std::sig_atomic_t shutdown_requested = 0;
inline void handle_stop_signal(int) { shutdown_requested = 1; }

// Serve until interrupted, then drain active handlers before logging their final state.
inline void run_service(grpc::Service &service, const Endpoint &listen, EventLog &log) {
    std::signal(SIGINT, handle_stop_signal);
    std::signal(SIGTERM, handle_stop_signal);
    grpc::ServerBuilder builder;
    // port zero lets tests ask the OS for an available port
    int bound_port = 0;
    builder.AddListeningPort(listen.address, grpc::InsecureServerCredentials(), &bound_port);
    builder.RegisterService(&service);
    auto server = builder.BuildAndStart();
    if (!server || bound_port == 0)
        throw std::runtime_error("cannot listen on " + listen.address);
    auto address =
        listen.address.substr(0, listen.address.rfind(':') + 1) + std::to_string(bound_port);
    log.info("gRPC listen " + address + " workers " + std::to_string(listen.workers));
    while (!shutdown_requested)
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    // let handlers finish before the caller logs counts and destroys the service
    server->Shutdown(rpc_deadline(5));
    server->Wait();
}
