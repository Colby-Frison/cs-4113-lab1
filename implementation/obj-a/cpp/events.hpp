#pragma once

#include <string>

// Course log line = process prefix + Lab 0 body.
//
//   {asctime} - {component}_pid{pid}@{host} - INFO - {body}
//
// component is "Q" for the queue or "sv" for a server function.
// The body helpers build:
//   {epoch:.3f} {job_id} IN {qid} {KIND} {detail}

class EventLog {
public:
    // Empty logfile => stderr. Otherwise append to that path.
    EventLog(const std::string& logfile, const std::string& component);
    ~EventLog();
    EventLog(const EventLog&) = delete;
    EventLog& operator=(const EventLog&) = delete;

    void info(const std::string& body);
    void error(const std::string& body);

    static std::string epoch_now();
    static std::string edge_label(const std::string& path);  // basename; host:port unchanged

    // Queue events
    static std::string arrive_via(
        const std::string& job_id, const std::string& qid, const std::string& source);
    static std::string depart_via(
        const std::string& job_id, const std::string& qid, const std::string& dest);
    static std::string error_queue_full(const std::string& job_id, const std::string& qid);

    // Server-function events (for later C++ SF / KV ports)
    static std::string enters(const std::string& job_id, const std::string& qid, int server_id);
    static std::string exits(const std::string& job_id, const std::string& qid, int server_id);
    static std::string task_start(
        const std::string& job_id, const std::string& qid, int tid, double seconds);
    static std::string task_end(
        const std::string& job_id, const std::string& qid, int tid, double seconds);
    static std::string no_out_edge(const std::string& job_id, const std::string& qid);

private:
    void write(const char* level, const std::string& body);
    static std::string event(
        const std::string& job_id,
        const std::string& qid,
        const std::string& kind,
        const std::string& detail);

    void* file_ = nullptr;  // FILE*; void* avoids including <cstdio> here
    bool owned_ = false;
    std::string component_;
};
