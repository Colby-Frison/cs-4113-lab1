#include "routing.hpp"

#include <algorithm>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace packets {

namespace {

uint32_t MaskFor(int length) {
    return length == 0 ? 0 : static_cast<uint32_t>(0xFFFFFFFFu << (32 - length));
}

int ParseInt(const std::string& text, int low, int high, const std::string& context) {
    size_t used = 0;
    int value = 0;
    try {
        value = std::stoi(text, &used);
    } catch (const std::exception&) {
        throw std::runtime_error("bad number in " + context);
    }
    if (used != text.size() || value < low || value > high) {
        throw std::runtime_error("bad number in " + context);
    }
    return value;
}

std::vector<std::string> Split(const std::string& text, char separator) {
    std::vector<std::string> pieces;
    std::string piece;
    std::istringstream stream(text);
    while (std::getline(stream, piece, separator)) {
        pieces.push_back(piece);
    }
    return pieces;
}

}  // namespace

std::pair<uint32_t, int> ParsePrefix(const std::string& text) {
    const auto parts = Split(text, '/');
    if (parts.size() != 2) {
        throw std::runtime_error("bad prefix " + text);
    }
    const auto octets = Split(parts[0], '.');
    if (octets.size() != 4) {
        throw std::runtime_error("bad prefix " + text);
    }
    uint32_t value = 0;
    for (const auto& octet : octets) {
        value = (value << 8) | static_cast<uint32_t>(ParseInt(octet, 0, 255, text));
    }
    const int length = ParseInt(parts[1], 0, 32, text);
    return {value & MaskFor(length), length};
}

RoutingTable::RoutingTable(const std::vector<std::pair<std::string, std::string>>& routes) {
    std::map<int, std::unordered_map<uint32_t, std::string>, std::greater<int>> by_length;
    for (const auto& [prefix, next_hop] : routes) {
        const auto [network, length] = ParsePrefix(prefix);
        by_length[length][network] = next_hop;
    }
    for (auto& [length, level] : by_length) {
        levels_.push_back(Level{MaskFor(length), std::move(level)});
    }
}

RoutingTable RoutingTable::Load(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open routing table " + path);
    }
    std::vector<std::pair<std::string, std::string>> routes;
    std::string line;
    int number = 0;
    while (std::getline(input, line)) {
        ++number;
        if (line.empty() || line[0] == '#') {
            continue;
        }
        const auto fields = Split(line, '\t');
        if (fields.size() != 2) {
            throw std::runtime_error(path + ":" + std::to_string(number) +
                                     ": expected prefix<TAB>next hop");
        }
        routes.emplace_back(fields[0], fields[1]);
    }
    return RoutingTable(routes);
}

std::optional<std::string> RoutingTable::Lookup(uint32_t dst_ip) const {
    for (const auto& level : levels_) {
        const auto found = level.routes.find(dst_ip & level.mask);
        if (found != level.routes.end()) {
            return found->second;
        }
    }
    return std::nullopt;
}

std::map<std::string, QueueAddress> LoadAddresses(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open addresses " + path);
    }
    std::map<std::string, QueueAddress> addresses;
    std::string line;
    std::getline(input, line);  // Header.
    while (std::getline(input, line)) {
        if (line.empty()) {
            continue;
        }
        const auto fields = Split(line, ',');
        if (fields.size() != 4) {
            throw std::runtime_error(path + ": expected qid,host,queue_port,server_port");
        }
        addresses[fields[0]] = QueueAddress{fields[1], ParseInt(fields[2], 0, 65535, path),
                                            ParseInt(fields[3], 0, 65535, path)};
    }
    return addresses;
}

}  // namespace packets
