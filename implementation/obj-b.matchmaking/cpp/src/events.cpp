#include "events.hpp"

#include "ticket.hpp"

#include <chrono>
#include <cstdio>
#include <ctime>
#include <sstream>
#include <stdexcept>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace {

std::string hostname() {
#ifdef _WIN32
    char buffer[256];
    DWORD size = sizeof(buffer);
    if (GetComputerNameA(buffer, &size)) {
        return std::string(buffer, size);
    }
    return "host";
#else
    char buffer[256];
    if (gethostname(buffer, sizeof(buffer)) == 0) {
        buffer[sizeof(buffer) - 1] = '\0';
        return buffer;
    }
    return "host";
#endif
}

long process_id() {
#ifdef _WIN32
    return static_cast<long>(GetCurrentProcessId());
#else
    return static_cast<long>(getpid());
#endif
}

std::string asctime_now() {
    const auto now = std::chrono::system_clock::now();
    const auto seconds = std::chrono::time_point_cast<std::chrono::seconds>(now);
    const auto millis =
        std::chrono::duration_cast<std::chrono::milliseconds>(now - seconds).count();
    const std::time_t clock = std::chrono::system_clock::to_time_t(now);
    std::tm local {};
#ifdef _WIN32
    localtime_s(&local, &clock);
#else
    localtime_r(&clock, &local);
#endif
    char date[32];
    std::strftime(date, sizeof(date), "%Y-%m-%d %H:%M:%S", &local);
    char line[48];
    std::snprintf(line, sizeof(line), "%s,%03d", date, static_cast<int>(millis));
    return line;
}

}  // namespace

EventLog::EventLog(const std::string& logfile) {
    if (logfile.empty()) {
        file_ = stderr;
        owned_ = false;
        return;
    }
    file_ = std::fopen(logfile.c_str(), "a");
    if (file_ == nullptr) {
        throw std::runtime_error("cannot open logfile " + logfile);
    }
    owned_ = true;
}

EventLog::~EventLog() {
    if (owned_ && file_ != nullptr) {
        std::fclose(static_cast<FILE*>(file_));
    }
}

void EventLog::info(const std::string& body) { write("INFO", body); }

void EventLog::error(const std::string& body) { write("ERROR", body); }

void EventLog::write(const char* level, const std::string& body) {
    auto* file = static_cast<FILE*>(file_);
    std::fprintf(
        file,
        "%s - sv_pid%ld@%s - %s - %s\n",
        asctime_now().c_str(),
        process_id(),
        hostname().c_str(),
        level,
        body.c_str());
    std::fflush(file);
}

std::string EventLog::epoch_now() {
    const auto now = std::chrono::system_clock::now();
    const double epoch = std::chrono::duration<double>(now.time_since_epoch()).count();
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%.3f", epoch);
    return buffer;
}

std::string EventLog::event(
    const std::string& job_id,
    const std::string& qid,
    const std::string& kind,
    const std::string& detail) {
    std::ostringstream line;
    line << epoch_now() << ' ' << job_id << " IN " << qid << ' ' << kind;
    if (!detail.empty()) {
        line << ' ' << detail;
    }
    return line.str();
}

std::string EventLog::enters(const std::string& job_id, const std::string& qid, int server_id) {
    return event(job_id, qid, "ENTERS-SERVER", std::to_string(server_id));
}

std::string EventLog::exits(const std::string& job_id, const std::string& qid, int server_id) {
    return event(job_id, qid, "EXITS-SERVER", std::to_string(server_id));
}

std::string EventLog::task_start(
    const std::string& job_id, const std::string& qid, int tid, double seconds) {
    return event(job_id, qid, "TASK-START", std::to_string(tid) + " \"" + format_wait(seconds) + "\"");
}

std::string EventLog::task_end(
    const std::string& job_id, const std::string& qid, int tid, double seconds) {
    return event(job_id, qid, "TASK-END", std::to_string(tid) + " \"" + format_wait(seconds) + "\"");
}

std::string EventLog::depart(const std::string& job_id, const std::string& qid, const std::string& dest) {
    return event(job_id, qid, "DEPART-VIA", edge_label(dest));
}

std::string EventLog::no_out_edge(const std::string& job_id, const std::string& qid) {
    return event(job_id, qid, "ERROR", "NO-OUT-EDGE");
}

std::string EventLog::edge_label(const std::string& path) {
    const auto slash = path.find_last_of("/\\");
    if (slash == std::string::npos) {
        return path;
    }
    const auto name = path.substr(slash + 1);
    return name.empty() ? path : name;
}
