#pragma once

#include <string>
#include <vector>

// Lab 0 wait-task job: one CSV row with columns
//   Job Id, arrival time, Tasks
// Tasks is a JSON list of "tid: W seconds" strings.
// raw_line is that row without a trailing newline — queues and server
// functions forward it unchanged so the next hop sees the same waits.

struct TaskWait {
    int tid = 0;
    double wait_seconds = 0.0;  // sleep duration at the server function
};

struct WaitJob {
    std::string job_id;
    double arrival_time = 0.0;
    std::vector<TaskWait> tasks;
    std::string raw_line;
};

struct ParseOutcome {
    enum class Status {
        ok,     // valid job
        skip,   // blank line or CSV header
        error,  // malformed row
    };
    Status status = Status::skip;
    WaitJob job;
    std::string error;
};

ParseOutcome parse_job_line(const std::string& line);
std::string format_wait(double seconds);           // "W 0.010000"
std::string format_job_line(const WaitJob& job);   // Lab 0 doubled-quote CSV
