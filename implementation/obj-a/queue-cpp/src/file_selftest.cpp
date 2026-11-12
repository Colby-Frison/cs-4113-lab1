// File-only smoke test (no gRPC link). Verifies the pieces that always build:
//   1. CSV parse + format round-trip keeps the job id
//   2. A size-1 buffer admits once, then drops with QUEUE-FULL
//   3. take() + DEPART-VIA write the original raw_line to the sink
//
// Usage: queue_file_selftest <jobs.csv> <sink>
// The gRPC binary is queue_server (skipped by CMake when packages are missing).

#include "buffer.hpp"
#include "events.hpp"
#include "job.hpp"

#include <cmath>
#include <fstream>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: queue_file_selftest <jobs.csv> <sink>\n";
        return 2;
    }

    // --- parse the first data row from the CSV ------------------------------
    std::ifstream input(argv[1]);
    if (!input) {
        std::cerr << "cannot open " << argv[1] << "\n";
        return 1;
    }

    std::string line;
    ParseOutcome parsed;
    parsed.status = ParseOutcome::Status::skip;
    while (std::getline(input, line)) {
        parsed = parse_job_line(line);
        // Skip blanks / header; stop on the first real job or a hard error.
        if (parsed.status != ParseOutcome::Status::skip) {
            break;
        }
    }
    if (parsed.status != ParseOutcome::Status::ok) {
        std::cerr << "parse: " << parsed.error << "\n";
        return 1;
    }

    // --- round-trip: format then parse again --------------------------------
    const auto again = parse_job_line(format_job_line(parsed.job));
    if (again.status != ParseOutcome::Status::ok || again.job.job_id != parsed.job.job_id) {
        std::cerr << "roundtrip failed\n";
        return 1;
    }

    // --- admit into a size-1 buffer, then force a drop ----------------------
    EventLog log("", "Q");  // empty path => stderr
    JobBuffer buffer(1);
    if (!admit_job(buffer, parsed.job, "q0", "jobs.csv", log)) {
        std::cerr << "first admit failed\n";
        return 1;
    }
    // Buffer is now full. A second admit must fail and log QUEUE-FULL.
    WaitJob overflow = parsed.job;
    overflow.job_id = "overflow";
    if (admit_job(buffer, overflow, "q0", "jobs.csv", log)) {
        std::cerr << "expected QUEUE-FULL drop\n";
        return 1;
    }

    // --- drain the one queued job to the sink -------------------------------
    std::ofstream out(argv[2], std::ios::binary | std::ios::app);
    if (!out) {
        std::cerr << "cannot open sink\n";
        return 1;
    }
    auto item = buffer.take();
    if (!item) {
        std::cerr << "expected queued job\n";
        return 1;
    }
    out << item->raw_line << '\n';
    log.info(EventLog::depart_via(item->job_id, "q0", argv[2]));

    std::cout << "id=" << parsed.job.job_id << " queue-full=ok roundtrip=ok\n";
    return 0;
}
