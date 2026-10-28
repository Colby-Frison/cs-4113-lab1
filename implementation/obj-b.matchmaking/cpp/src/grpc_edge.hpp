#pragma once

#include "events.hpp"
#include "ticket.hpp"

#include <functional>
#include <string>

struct DialTarget {
    std::string host;
    int port = 0;
    int workers = 1;
    std::string address;
};

struct ListenTarget {
    std::string host;
    int port = 0;
    int workers = 1;
    std::string address;
};

bool parse_listen_target(const std::string& text, ListenTarget* out, std::string* error);
bool parse_dial_target(const std::string& text, DialTarget* out, std::string* error);

// QueueToServer server and ServerToQueue client. Submit on the listen port
// runs the visit before it returns. A failed client RPC is logged here.
class GrpcHub {
public:
    explicit GrpcHub(EventLog& log);

    bool submit(const DialTarget& dial, const TicketJob& job, const std::string& source_edge);
    void serve_until_interrupt(
        const ListenTarget& listen, const std::function<void(const TicketJob&)>& visit);

private:
    EventLog& log_;
};
