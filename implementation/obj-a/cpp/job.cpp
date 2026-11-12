// CSV parse/format for Lab 0 wait-task rows. Matches Python lib/job.py.
//
// A job row looks like:
//   j0,0.000,"[""0: W 0.010000""]"
// After CSV unquoting the Tasks field is:
//   ["0: W 0.010000"]
// Each list element is "tid: W seconds". The queue forwards raw_line as-is.

#include "job.hpp"

#include <cmath>
#include <cstdio>
#include <regex>
#include <sstream>

namespace {

// tid, optional spaces, "W", optional spaces, non-negative number.
const std::regex kTask(R"(^(\d+):\s*W\s*([0-9]+(?:\.[0-9]+)?)$)");

// Split one CSV row into cells. Handles:
//   - commas outside quotes as separators
//   - "..." quoted fields
//   - "" inside a quoted field as a literal quote (Lab 0 / Python csv style)
std::vector<std::string> split_csv(const std::string& line, std::string* error) {
    std::vector<std::string> columns;
    std::string cell;
    bool in_quotes = false;
    for (size_t i = 0; i < line.size(); ++i) {
        const char ch = line[i];
        if (in_quotes) {
            if (ch == '"') {
                // Doubled quote => one literal ". Lone quote => end of field.
                if (i + 1 < line.size() && line[i + 1] == '"') {
                    cell.push_back('"');
                    ++i;
                } else {
                    in_quotes = false;
                }
            } else {
                cell.push_back(ch);
            }
            continue;
        }
        if (ch == '"') {
            in_quotes = true;
            continue;
        }
        if (ch == ',') {
            columns.push_back(cell);
            cell.clear();
            continue;
        }
        cell.push_back(ch);
    }
    if (in_quotes) {
        *error = "unterminated quote";
        return {};
    }
    // Last cell (even if the line ended on a comma, that already pushed an empty one).
    columns.push_back(cell);
    return columns;
}

// Walk a JSON-ish list of quoted task strings. We only accept the Lab 0 shape
// and do not depend on a full JSON library.
std::vector<TaskWait> parse_tasks(const std::string& json, std::string* error) {
    std::vector<TaskWait> tasks;
    size_t i = 0;
    auto skip = [&]() {
        while (i < json.size() && (json[i] == ' ' || json[i] == '\t')) {
            ++i;
        }
    };

    skip();
    if (i >= json.size() || json[i] != '[') {
        *error = "tasks are not a JSON list";
        return {};
    }
    ++i;  // past '['
    skip();
    if (i < json.size() && json[i] == ']') {
        *error = "tasks must be a non-empty JSON list";
        return {};
    }

    while (i < json.size()) {
        skip();
        // Expect "task string"
        if (json[i] != '"') {
            *error = "each task must be a string";
            return {};
        }
        ++i;
        std::string item;
        while (i < json.size() && json[i] != '"') {
            // Allow a simple backslash escape if present.
            if (json[i] == '\\' && i + 1 < json.size()) {
                item.push_back(json[i + 1]);
                i += 2;
                continue;
            }
            item.push_back(json[i]);
            ++i;
        }
        if (i >= json.size() || json[i] != '"') {
            *error = "unterminated task string";
            return {};
        }
        ++i;  // past closing '"'

        std::smatch match;
        if (!std::regex_match(item, match, kTask)) {
            *error = "unrecognized task " + item;
            return {};
        }
        TaskWait task;
        task.tid = std::stoi(match[1].str());
        task.wait_seconds = std::stod(match[2].str());
        if (!std::isfinite(task.wait_seconds) || task.wait_seconds < 0) {
            *error = "wait must be a finite number >= 0";
            return {};
        }
        tasks.push_back(task);

        skip();
        if (i < json.size() && json[i] == ',') {
            ++i;  // more tasks
            continue;
        }
        if (i < json.size() && json[i] == ']') {
            ++i;  // end of list
            break;
        }
        *error = "tasks are not a JSON list";
        return {};
    }

    skip();
    // Must have consumed the whole string and produced at least one task.
    if (i != json.size() || tasks.empty()) {
        *error = "tasks are not a JSON list";
        return {};
    }
    return tasks;
}

}  // namespace

std::string format_wait(double seconds) {
    // Same six-decimal token used in TASK-START / TASK-END and in the CSV.
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "W %.6f", seconds);
    return buffer;
}

ParseOutcome parse_job_line(const std::string& line) {
    ParseOutcome outcome;

    // Strip CR/LF so Windows and Unix rows parse the same.
    std::string raw = line;
    while (!raw.empty() && (raw.back() == '\n' || raw.back() == '\r')) {
        raw.pop_back();
    }

    // Blank lines and the workload header are ignored by producers.
    if (raw.empty() || raw == "Job Id,arrival time,Tasks") {
        outcome.status = ParseOutcome::Status::skip;
        return outcome;
    }

    std::string error;
    auto columns = split_csv(raw, &error);
    if (!error.empty()) {
        outcome.status = ParseOutcome::Status::error;
        outcome.error = error;
        return outcome;
    }
    if (columns.size() != 3) {
        outcome.status = ParseOutcome::Status::error;
        outcome.error = "expected 3 columns";
        return outcome;
    }
    if (columns[0].empty()) {
        outcome.status = ParseOutcome::Status::error;
        outcome.error = "missing job id";
        return outcome;
    }

    try {
        outcome.job.arrival_time = std::stod(columns[1]);
    } catch (const std::exception&) {
        outcome.status = ParseOutcome::Status::error;
        outcome.error = "arrival time is not a number";
        return outcome;
    }
    if (!std::isfinite(outcome.job.arrival_time) || outcome.job.arrival_time < 0) {
        outcome.status = ParseOutcome::Status::error;
        outcome.error = "arrival time must be a finite number >= 0";
        return outcome;
    }

    outcome.job.tasks = parse_tasks(columns[2], &error);
    if (!error.empty()) {
        outcome.status = ParseOutcome::Status::error;
        outcome.error = error;
        return outcome;
    }

    outcome.job.job_id = columns[0];
    // Keep the exact input text so a later hop can re-parse the same waits.
    outcome.job.raw_line = raw;
    outcome.status = ParseOutcome::Status::ok;
    return outcome;
}

std::string format_job_line(const WaitJob& job) {
    // Build the Tasks JSON list first: ["0: W 0.010000", ...]
    std::ostringstream tasks;
    tasks << '[';
    for (size_t i = 0; i < job.tasks.size(); ++i) {
        if (i != 0) {
            tasks << ", ";
        }
        char body[128];
        std::snprintf(
            body,
            sizeof(body),
            "%d: W %.6f",
            job.tasks[i].tid,
            job.tasks[i].wait_seconds);
        tasks << '"' << body << '"';
    }
    tasks << ']';

    // Wrap that whole list as one CSV field: quotes inside become "".
    std::string quoted;
    for (char ch : tasks.str()) {
        if (ch == '"') {
            quoted += "\"\"";
        } else {
            quoted.push_back(ch);
        }
    }

    char arrival[64];
    std::snprintf(arrival, sizeof(arrival), "%.3f", job.arrival_time);
    return job.job_id + "," + arrival + ",\"" + quoted + "\"";
}
