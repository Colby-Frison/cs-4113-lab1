// shared CSV validation and event formatting for serial and hosted servers
#pragma once
#include <grpcpp/grpcpp.h>
#include <google/protobuf/struct.pb.h>
#include <google/protobuf/util/json_util.h>
#include "queue_to_server.grpc.pb.h"
#include "server_to_queue.grpc.pb.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::system_clock;
using Steady = std::chrono::steady_clock;

std::string number(double value) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(3) << value;
    return out.str();
}

// Parse RFC-style CSV quoting, including doubled quotes in the Tasks JSON.
std::vector<std::string> csv_fields(const std::string &row) {
    if (row.find_first_of("\r\n") != std::string::npos)
        throw std::invalid_argument("job row must be a single line");
    std::vector<std::string> fields;
    size_t pos = 0;
    while (true) {
        std::string field;
        if (pos < row.size() && row[pos] == '"') {
            ++pos;
            bool closed = false;
            while (pos < row.size()) {
                char c = row[pos++];
                if (c != '"')
                    field += c;
                else if (pos < row.size() && row[pos] == '"') {
                    field += '"';
                    ++pos;
                } else {
                    closed = true;
                    break;
                }
            }
            if (!closed)
                throw std::invalid_argument("unclosed CSV quote");
        } else {
            while (pos < row.size() && row[pos] != ',') {
                if (row[pos] == '"')
                    throw std::invalid_argument("unexpected CSV quote");
                field += row[pos++];
            }
        }
        fields.push_back(field);
        if (pos == row.size())
            break;
        if (row[pos++] != ',')
            throw std::invalid_argument("expected CSV separator");
    }
    return fields;
}

double seconds(const std::string &text) {
    size_t used = 0;
    double value;
    try {
        value = std::stod(text, &used);
    } catch (const std::exception &) {
        throw std::invalid_argument("invalid duration/time");
    }
    if (used != text.size() || !std::isfinite(value) || value < 0)
        throw std::invalid_argument("time must be finite and nonnegative");
    return value;
}

Job parse_row(const std::string &row) {
    const auto fields = csv_fields(row);
    if (fields.size() != 3)
        throw std::invalid_argument("expected three CSV fields");
    Job job;
    job.set_job_id(fields[0]);
    job.set_arrival_time(seconds(fields[1]));
    job.set_raw_line(row);
    google::protobuf::ListValue tasks;
    if (!google::protobuf::util::JsonStringToMessage(fields[2], &tasks).ok())
        throw std::invalid_argument("invalid task JSON list");
    static const std::regex task_pattern(R"(^([01]):\s*W\s*([0-9]+(?:\.[0-9]+)?)$)");
    for (const auto &value : tasks.values()) {
        std::smatch match;
        const auto text = value.string_value();
        if (value.kind_case() != google::protobuf::Value::kStringValue ||
            !std::regex_match(text, match, task_pattern))
            throw std::invalid_argument("invalid wait task");
        auto *task = job.add_tasks();
        task->set_tid(std::stoi(match[1]));
        task->set_wait_seconds(seconds(match[2]));
    }
    return job;
}

Job validate_request(const Job &message, const std::set<std::string> &restaurants) {
    Job job = message;
    if (!message.raw_line().empty()) {
        job = parse_row(message.raw_line());
        // callers can send just CSV or both forms, but both must describe the same order
        if (!message.job_id().empty() || message.tasks_size() != 0) {
            if (message.job_id() != job.job_id() || message.arrival_time() != job.arrival_time() ||
                message.tasks_size() != job.tasks_size())
                throw std::invalid_argument("structured job disagrees with CSV");
            for (int i = 0; i < job.tasks_size(); ++i)
                if (message.tasks(i).tid() != job.tasks(i).tid() ||
                    message.tasks(i).wait_seconds() != job.tasks(i).wait_seconds())
                    throw std::invalid_argument("structured tasks disagree with CSV");
        }
    }
    static const std::regex order_pattern(R"(^o[0-9]+\.r([0-9]+)\.(near|mid|far)\.i[1-9][0-9]*$)");
    std::smatch match;
    const std::string id = job.job_id();
    if (!std::regex_match(id, match, order_pattern) || !restaurants.count(match[1].str()))
        throw std::invalid_argument("invalid food order ID or unsupported restaurant");
    if (!std::isfinite(job.arrival_time()) || job.arrival_time() < 0 || job.tasks_size() != 2)
        throw std::invalid_argument("invalid arrival time or task count");
    for (int i = 0; i < 2; ++i) {
        double wait = job.tasks(i).wait_seconds();
        if (job.tasks(i).tid() != i || !std::isfinite(wait) || wait < 0)
            throw std::invalid_argument(
                "expected nonnegative preparation task 0 then delivery task 1");
    }
    if (job.raw_line().empty()) {
        // normalize structured-only clients to the same precision as CSV workloads
        std::string row = job.job_id() + "," + number(job.arrival_time()) + ",\"[\"\"0: W " +
                          number(job.tasks(0).wait_seconds()) + "\"\", \"\"1: W " +
                          number(job.tasks(1).wait_seconds()) + "\"\"]\"";
        job = parse_row(row);
    }
    return job;
}

class Logger {
    std::ofstream file_;

  public:
    explicit Logger(const std::string &path) : file_(path) {
        file_.exceptions(std::ios::failbit | std::ios::badbit);
    }
    void message(const std::string &text) {
        auto now = Clock::now();
        auto stamp = Clock::to_time_t(now);
        std::tm local{};
        localtime_r(&stamp, &local);
        auto millis =
            std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() %
            1000;
        file_ << std::put_time(&local, "%Y-%m-%d %H:%M:%S") << ',' << std::setfill('0')
              << std::setw(3) << millis << " - sv - INFO - " << text << '\n';
        file_.flush();
    }
    void event(const Job &job, const std::string &qid, const std::string &kind,
               const std::string &detail) {
        const double now = std::chrono::duration<double>(Clock::now().time_since_epoch()).count();
        message(number(now) + " " + job.job_id() + " IN " + qid + " " + kind + " " + detail);
    }
};
