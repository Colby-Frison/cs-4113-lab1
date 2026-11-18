// Routing tables and the queue address book. Same file formats as pk/routing.py.
#pragma once

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace packets {

inline const std::string kLocal = "local";

// Longest-prefix match over one map per prefix length.
class RoutingTable {
public:
    RoutingTable() = default;
    explicit RoutingTable(const std::vector<std::pair<std::string, std::string>>& routes);

    // Reads prefix<TAB>next hop lines. Throws std::runtime_error on a bad line.
    static RoutingTable Load(const std::string& path);

    std::optional<std::string> Lookup(uint32_t dst_ip) const;

private:
    struct Level {
        uint32_t mask;
        std::unordered_map<uint32_t, std::string> routes;
    };
    std::vector<Level> levels_;  // Longest prefix first.
};

struct QueueAddress {
    std::string host;
    int queue_port;
    int server_port;
};

// Reads addresses.csv: qid,host,queue_port,server_port.
std::map<std::string, QueueAddress> LoadAddresses(const std::string& path);

// Parses a.b.c.d/n into its masked network and its length.
std::pair<uint32_t, int> ParsePrefix(const std::string& text);

}  // namespace packets
