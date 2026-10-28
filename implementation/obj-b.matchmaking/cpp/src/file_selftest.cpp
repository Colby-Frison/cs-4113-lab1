#include "events.hpp"
#include "ticket.hpp"
#include "visit.hpp"

#include <cmath>
#include <fstream>
#include <iostream>
#include <random>
#include <string>

// File-only check of the CSV parser and one stage visit. The gRPC binary is
// match_server; this target links none of that stack.

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: match_file_selftest <csv-line> <sink>\n";
        return 2;
    }
    const auto parsed = parse_job_line(argv[1]);
    if (parsed.status != ParseOutcome::Status::ok) {
        std::cerr << "parse: " << parsed.error << "\n";
        return 1;
    }
    const auto formatted = format_job_line(parsed.job);
    const auto again = parse_job_line(formatted);
    if (again.status != ParseOutcome::Status::ok || again.job.job_id != parsed.job.job_id ||
        again.job.tasks.size() != parsed.job.tasks.size()) {
        std::cerr << "roundtrip failed\n";
        return 1;
    }
    for (size_t i = 0; i < parsed.job.tasks.size(); ++i) {
        if (again.job.tasks[i].tid != parsed.job.tasks[i].tid) {
            std::cerr << "roundtrip tid\n";
            return 1;
        }
        if (std::fabs(again.job.tasks[i].wait_seconds - parsed.job.tasks[i].wait_seconds) > 1e-6) {
            std::cerr << "roundtrip wait\n";
            return 1;
        }
    }

    const std::string sink = argv[2];
    std::ofstream out(sink, std::ios::binary | std::ios::app);
    if (!out) {
        std::cerr << "cannot open sink\n";
        return 1;
    }
    EventLog log("");
    std::mt19937 rng(1);
    visit_job(
        log,
        parsed.job,
        "q-search",
        0,
        3,
        [&](const TicketJob&, std::mt19937&) { return std::optional<std::string>(sink); },
        [&](const std::string& dest, const TicketJob& job) {
            if (dest != sink) {
                return ForwardResult::missing;
            }
            out << job.raw_line << '\n';
            out.flush();
            return ForwardResult::sent;
        },
        rng,
        nullptr,
        nullptr);
    std::cout << "id=" << parsed.job.job_id << "\n";
    std::cout << "tasks=" << parsed.job.tasks.size() << "\n";
    std::cout << "roundtrip=ok\n";
    return 0;
}
