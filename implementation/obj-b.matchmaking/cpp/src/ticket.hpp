#pragma once

#include <optional>
#include <string>
#include <vector>

// A party ticket is the job id t{seq}.{region}.{mode}.{band}.p{party}.
// The CSV row is the Lab 0 three-column line. raw_line is that row without
// the trailing newline, and it is what a stage forwards.

struct TaskWait {
    int tid = 0;
    double wait_seconds = 0.0;
};

struct TicketJob {
    std::string job_id;
    double arrival_time = 0.0;
    std::vector<TaskWait> tasks;
    std::string raw_line;
};

struct Ticket {
    int seq = 0;
    std::string region;
    std::string mode;
    std::string band;
    int party = 0;
};

struct ParseOutcome {
    enum class Status { ok, skip, error };
    Status status = Status::skip;
    TicketJob job;
    std::string error;
};

std::optional<Ticket> parse_ticket(const std::string& job_id);
ParseOutcome parse_job_line(const std::string& line);
std::string format_wait(double seconds);
std::string format_job_line(const TicketJob& job);
