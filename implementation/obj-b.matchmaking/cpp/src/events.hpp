#pragma once

#include <string>

// Course prefix plus a Lab 0 body:
//   {asctime} - sv_pid{pid}@{host} - INFO - {epoch} {job} IN {qid} {KIND} ...

class EventLog {
public:
    explicit EventLog(const std::string& logfile);
    ~EventLog();
    EventLog(const EventLog&) = delete;
    EventLog& operator=(const EventLog&) = delete;

    void info(const std::string& body);
    void error(const std::string& body);

    static std::string epoch_now();
    static std::string enters(const std::string& job_id, const std::string& qid, int server_id);
    static std::string exits(const std::string& job_id, const std::string& qid, int server_id);
    static std::string task_start(
        const std::string& job_id, const std::string& qid, int tid, double seconds);
    static std::string task_end(
        const std::string& job_id, const std::string& qid, int tid, double seconds);
    static std::string depart(const std::string& job_id, const std::string& qid, const std::string& dest);
    static std::string no_out_edge(const std::string& job_id, const std::string& qid);
    static std::string edge_label(const std::string& path);

private:
    void write(const char* level, const std::string& body);
    static std::string event(
        const std::string& job_id,
        const std::string& qid,
        const std::string& kind,
        const std::string& detail);

    void* file_ = nullptr;
    bool owned_ = false;
};
