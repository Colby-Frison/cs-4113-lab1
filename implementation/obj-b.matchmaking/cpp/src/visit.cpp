#include "visit.hpp"

#include <chrono>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <thread>

void visit_job(
    EventLog& log,
    const TicketJob& job,
    const std::string& qid,
    int server_id,
    int only_tid,
    const EdgeChooser& chooser,
    const ForwardFn& forward,
    std::mt19937& rng,
    std::map<std::string, int>* state,
    std::mutex* state_mu) {
    log.info(EventLog::enters(job.job_id, qid, server_id));
    if (state != nullptr) {
        if (state_mu != nullptr) {
            std::lock_guard<std::mutex> guard(*state_mu);
            (*state)[job.job_id] += 1;
        } else {
            (*state)[job.job_id] += 1;
        }
    }

    bool slept = false;
    for (const auto& task : job.tasks) {
        if (task.tid != only_tid) {
            continue;
        }
        log.info(EventLog::task_start(job.job_id, qid, task.tid, task.wait_seconds));
        if (task.wait_seconds > 0) {
            const auto micros = static_cast<long long>(task.wait_seconds * 1'000'000.0);
            std::this_thread::sleep_for(std::chrono::microseconds(micros));
        }
        log.info(EventLog::task_end(job.job_id, qid, task.tid, task.wait_seconds));
        slept = true;
    }
    if (!slept) {
        log.error("stage has no task tid " + std::to_string(only_tid) + " on " + job.job_id);
    }
    log.info(EventLog::exits(job.job_id, qid, server_id));

    const auto dest = chooser(job, rng);
    if (!dest) {
        log.info(EventLog::no_out_edge(job.job_id, qid));
        return;
    }
    const auto result = forward(*dest, job);
    if (result == ForwardResult::missing) {
        log.error("edge chooser returned " + *dest + ", which is not an open edge");
        log.info(EventLog::no_out_edge(job.job_id, qid));
        return;
    }
    if (result == ForwardResult::failed) {
        return;
    }
    log.info(EventLog::depart(job.job_id, qid, *dest));
}

std::optional<std::string> choose_ready(
    int visits,
    std::mt19937& rng,
    double fail_p,
    int max_visits,
    const std::string& pass_dest,
    const std::string& retry_dest,
    const std::string& fail_dest) {
    std::uniform_real_distribution<double> unit(0.0, 1.0);
    if (unit(rng) < fail_p) {
        if (visits >= max_visits) {
            return fail_dest;
        }
        return retry_dest;
    }
    return pass_dest;
}

int stage_tid(const std::string& stage) {
    if (stage == "validate") return 0;
    if (stage == "rating") return 1;
    if (stage == "route") return 2;
    if (stage == "search") return 3;
    if (stage == "ready") return 4;
    if (stage == "allocate") return 5;
    throw std::invalid_argument("unknown stage " + stage);
}

bool known_stage(const std::string& stage) {
    return stage == "validate" || stage == "rating" || stage == "route" || stage == "search" ||
           stage == "ready" || stage == "allocate";
}

std::map<std::string, int> load_state(const std::string& path) {
    std::map<std::string, int> state;
    std::ifstream input(path);
    if (!input) {
        return state;
    }
    std::string line;
    int number = 0;
    while (std::getline(input, line)) {
        ++number;
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line.empty()) {
            continue;
        }
        std::istringstream row(line);
        std::string job_id;
        int count = 0;
        if (!(row >> job_id >> count) || count < 0) {
            throw std::runtime_error(path + ":" + std::to_string(number) + " is not 'job_id visits'");
        }
        state[job_id] = count;
    }
    return state;
}

void save_state(const std::string& path, const std::map<std::string, int>& state) {
    const auto temporary = path + ".tmp";
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
    if (!output) {
        throw std::runtime_error("cannot write " + temporary);
    }
    for (const auto& item : state) {
        output << item.first << ' ' << item.second << '\n';
    }
    output.close();
    if (std::rename(temporary.c_str(), path.c_str()) != 0) {
        std::remove(path.c_str());
        if (std::rename(temporary.c_str(), path.c_str()) != 0) {
            throw std::runtime_error("cannot replace " + path);
        }
    }
}
