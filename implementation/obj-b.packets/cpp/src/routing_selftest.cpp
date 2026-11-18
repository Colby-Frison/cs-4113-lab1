// Checks the routing table without gRPC. Exits non-zero on the first failure.
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

#include "routing.hpp"

namespace {

int failures = 0;

void Check(bool condition, const std::string& name) {
    if (!condition) {
        std::cerr << "FAILED: " << name << "\n";
        ++failures;
    }
}

constexpr uint32_t Address(uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
    return (a << 24) | (b << 16) | (c << 8) | d;
}

}  // namespace

int main() {
    using packets::RoutingTable;
    const RoutingTable table({
        {"10.0.0.0/8", "q1"},
        {"10.3.0.0/16", "q2"},
        {"10.3.144.0/24", "q3"},
        {"10.0.17.0/24", "local"},
    });
    Check(table.Lookup(Address(10, 3, 144, 7)) == "q3", "longest prefix wins");
    Check(table.Lookup(Address(10, 3, 200, 7)) == "q2", "falls back to the /16");
    Check(table.Lookup(Address(10, 9, 9, 9)) == "q1", "falls back to the /8");
    Check(table.Lookup(Address(10, 0, 17, 1)) == "local", "local route");
    Check(!table.Lookup(Address(192, 168, 0, 1)).has_value(), "no route");

    const RoutingTable everything({{"0.0.0.0/0", "q9"}});
    Check(everything.Lookup(Address(1, 2, 3, 4)) == "q9", "default route");

    bool rejected = false;
    try {
        packets::ParsePrefix("10.0.0.256/24");
    } catch (const std::runtime_error&) {
        rejected = true;
    }
    Check(rejected, "bad octet is rejected");

    if (failures == 0) {
        std::cout << "routing self-test passed\n";
    }
    return failures == 0 ? 0 : 1;
}
