#pragma once

#include "events.hpp"
#include "ticket.hpp"

#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <random>
#include <string>

// One stage visit. only_tid is the task this process sleeps. state, when
// set, counts entries for the ready-check cap and is incremented before the
// chooser runs. ForwardResult::failed is a transport error and skips
// DEPART-VIA. A chooser that returns nullopt, or a dest that is not open,
// logs ERROR NO-OUT-EDGE.

enum class ForwardResult { sent, failed, missing };

using EdgeChooser = std::function<std::optional<std::string>(const TicketJob&, std::mt19937&)>;
using ForwardFn = std::function<ForwardResult(const std::string& dest, const TicketJob& job)>;

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
    std::mutex* state_mu);

std::optional<std::string> choose_ready(
    int visits,
    std::mt19937& rng,
    double fail_p,
    int max_visits,
    const std::string& pass_dest,
    const std::string& retry_dest,
    const std::string& fail_dest);

int stage_tid(const std::string& stage);
bool known_stage(const std::string& stage);

std::map<std::string, int> load_state(const std::string& path);
void save_state(const std::string& path, const std::map<std::string, int>& state);
