#include "events.hpp"
#include "grpc_edge.hpp"
#include "ticket.hpp"
#include "visit.hpp"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <optional>
#include <random>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace {

struct Options {
    std::string stage;
    std::string infile;
    std::string ingrpc;
    std::vector<std::string> outfiles;
    std::vector<std::string> outgrpc;
    std::vector<std::string> route_edges;
    std::string pass_dest;
    std::string retry_dest;
    std::string fail_dest;
    std::string qid = "q0";
    int server_id = 0;
    std::string logfile;
    std::optional<unsigned> seed;
    double ready_fail_p = 0.2;
    int max_ready_visits = 2;
    std::string state_file;
};

void usage() {
    std::cerr
        << "match_server --stage validate|rating|route|search|ready|allocate\n"
        << "  (-i INFILE | --ingrpc interface:port,workers)\n"
        << "  [-o OUTFILE | --outgrpc host:port,workers]\n"
        << "  [--route-edge region=path | --route-edge region=host:port,workers]\n"
        << "  [--pass DEST --retry DEST --fail DEST]\n"
        << "  [--ready-fail-p P] [--max-ready-visits N] [--state-file PATH]\n"
        << "  [--seed N] [--qid ID] [--server-id N] [--logfile PATH]\n";
}

bool take_value(int argc, char** argv, int* index, std::string* value) {
    if (*index + 1 >= argc) {
        return false;
    }
    *value = argv[++(*index)];
    return true;
}

Options parse_args(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string flag = argv[i];
        std::string value;
        if (flag == "--help" || flag == "-h") {
            usage();
            std::exit(0);
        } else if (flag == "--stage") {
            if (!take_value(argc, argv, &i, &options.stage)) {
                throw std::runtime_error("--stage needs a value");
            }
        } else if (flag == "-i" || flag == "--infile") {
            if (!take_value(argc, argv, &i, &options.infile)) {
                throw std::runtime_error("--infile needs a value");
            }
        } else if (flag == "--ingrpc") {
            if (!take_value(argc, argv, &i, &options.ingrpc)) {
                throw std::runtime_error("--ingrpc needs a value");
            }
        } else if (flag == "-o" || flag == "--outfile") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--outfile needs a value");
            }
            options.outfiles.push_back(value);
        } else if (flag == "--outgrpc") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--outgrpc needs a value");
            }
            options.outgrpc.push_back(value);
        } else if (flag == "--route-edge") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--route-edge needs a value");
            }
            options.route_edges.push_back(value);
        } else if (flag == "--pass") {
            if (!take_value(argc, argv, &i, &options.pass_dest)) {
                throw std::runtime_error("--pass needs a value");
            }
        } else if (flag == "--retry") {
            if (!take_value(argc, argv, &i, &options.retry_dest)) {
                throw std::runtime_error("--retry needs a value");
            }
        } else if (flag == "--fail") {
            if (!take_value(argc, argv, &i, &options.fail_dest)) {
                throw std::runtime_error("--fail needs a value");
            }
        } else if (flag == "--qid") {
            if (!take_value(argc, argv, &i, &options.qid)) {
                throw std::runtime_error("--qid needs a value");
            }
        } else if (flag == "--server-id") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--server-id needs a value");
            }
            options.server_id = std::stoi(value);
        } else if (flag == "--logfile") {
            if (!take_value(argc, argv, &i, &options.logfile)) {
                throw std::runtime_error("--logfile needs a value");
            }
        } else if (flag == "--seed") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--seed needs a value");
            }
            options.seed = static_cast<unsigned>(std::stoul(value));
        } else if (flag == "--ready-fail-p") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--ready-fail-p needs a value");
            }
            options.ready_fail_p = std::stod(value);
        } else if (flag == "--max-ready-visits") {
            if (!take_value(argc, argv, &i, &value)) {
                throw std::runtime_error("--max-ready-visits needs a value");
            }
            options.max_ready_visits = std::stoi(value);
        } else if (flag == "--state-file") {
            if (!take_value(argc, argv, &i, &options.state_file)) {
                throw std::runtime_error("--state-file needs a value");
            }
        } else {
            throw std::runtime_error("unknown flag " + flag);
        }
    }
    return options;
}

struct Dest {
    std::string label;
    bool grpc = false;
    DialTarget dial;
};

Dest classify_dest(const std::string& text) {
    Dest dest;
    if (text.find(',') == std::string::npos) {
        dest.label = text;
        dest.grpc = false;
        return dest;
    }
    std::string error;
    if (!parse_dial_target(text, &dest.dial, &error)) {
        throw std::runtime_error(error);
    }
    dest.label = dest.dial.address;
    dest.grpc = true;
    return dest;
}

struct Edges {
    std::map<std::string, std::string> routes;
    std::string pass_label;
    std::string retry_label;
    std::string fail_label;
    std::map<std::string, std::ofstream> files;
    std::map<std::string, DialTarget> dials;
    std::vector<std::string> labels;
};

void remember(Edges* edges, const Dest& dest) {
    edges->labels.push_back(dest.label);
    if (dest.grpc) {
        edges->dials[dest.label] = dest.dial;
        return;
    }
    std::ofstream out(dest.label, std::ios::binary | std::ios::app);
    if (!out) {
        throw std::runtime_error("cannot open " + dest.label);
    }
    edges->files.emplace(dest.label, std::move(out));
}

Edges build_edges(const Options& options) {
    Edges edges;
    if (options.stage == "route") {
        if (options.route_edges.empty()) {
            throw std::runtime_error("--stage route requires at least one --route-edge");
        }
        for (const auto& item : options.route_edges) {
            const auto eq = item.find('=');
            if (eq == std::string::npos || eq == 0 || eq + 1 >= item.size()) {
                throw std::runtime_error("--route-edge must be region=dest");
            }
            const auto dest = classify_dest(item.substr(eq + 1));
            edges.routes[item.substr(0, eq)] = dest.label;
            remember(&edges, dest);
        }
        return edges;
    }
    if (options.stage == "ready") {
        if (options.ready_fail_p < 0 || options.ready_fail_p > 1) {
            throw std::runtime_error("--ready-fail-p must be between 0 and 1");
        }
        if (options.max_ready_visits < 1) {
            throw std::runtime_error("--max-ready-visits must be >= 1");
        }
        if (options.pass_dest.empty() || options.retry_dest.empty() || options.fail_dest.empty()) {
            throw std::runtime_error("--stage ready requires --pass, --retry, and --fail");
        }
        if (options.pass_dest == options.retry_dest || options.pass_dest == options.fail_dest ||
            options.retry_dest == options.fail_dest) {
            throw std::runtime_error("--pass, --retry, and --fail must be three different destinations");
        }
        const Dest pass = classify_dest(options.pass_dest);
        const Dest retry = classify_dest(options.retry_dest);
        const Dest fail = classify_dest(options.fail_dest);
        edges.pass_label = pass.label;
        edges.retry_label = retry.label;
        edges.fail_label = fail.label;
        remember(&edges, pass);
        remember(&edges, retry);
        remember(&edges, fail);
        return edges;
    }
    if (!options.route_edges.empty() || !options.pass_dest.empty()) {
        throw std::runtime_error("--stage " + options.stage + " does not take route or ready edges");
    }
    if (options.outfiles.size() + options.outgrpc.size() != 1) {
        throw std::runtime_error("--stage " + options.stage + " requires exactly one --outfile or --outgrpc");
    }
    if (!options.outfiles.empty()) {
        remember(&edges, classify_dest(options.outfiles[0]));
    } else {
        remember(&edges, classify_dest(options.outgrpc[0]));
    }
    return edges;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2) {
            usage();
            return 2;
        }
        const Options options = parse_args(argc, argv);
        if (!known_stage(options.stage)) {
            throw std::runtime_error("--stage must be validate, rating, route, search, ready, or allocate");
        }
        if (options.server_id < 0) {
            throw std::runtime_error("--server-id must be >= 0");
        }
        if (options.qid.empty()) {
            throw std::runtime_error("--qid must be non-empty");
        }
        if (!options.infile.empty() && !options.ingrpc.empty()) {
            throw std::runtime_error("pass either --infile or --ingrpc, not both");
        }
        if (options.infile.empty() && options.ingrpc.empty()) {
            throw std::runtime_error("need --infile or --ingrpc");
        }

        Edges edges = build_edges(options);
        EventLog log(options.logfile);
        log.info("Started");
        log.info("stage " + options.stage + " qid " + options.qid + " server-id " + std::to_string(options.server_id));

        std::map<std::string, int> state;
        std::map<std::string, int>* state_ptr = nullptr;
        std::mutex state_mu;
        if (options.stage == "ready") {
            if (!options.state_file.empty()) {
                state = load_state(options.state_file);
            }
            state_ptr = &state;
        }
        std::mt19937 rng(options.seed.value_or(std::random_device {}()));
        const int tid = stage_tid(options.stage);
        GrpcHub hub(log);

        EdgeChooser chooser = [&](const TicketJob& job, std::mt19937& roll) -> std::optional<std::string> {
            if (options.stage == "route") {
                const auto ticket = parse_ticket(job.job_id);
                if (!ticket) {
                    return std::nullopt;
                }
                const auto found = edges.routes.find(ticket->region);
                if (found == edges.routes.end()) {
                    return std::nullopt;
                }
                return found->second;
            }
            if (options.stage == "ready") {
                int visits = 1;
                {
                    std::lock_guard<std::mutex> guard(state_mu);
                    const auto found = state.find(job.job_id);
                    if (found != state.end()) {
                        visits = found->second;
                    }
                }
                return choose_ready(
                    visits,
                    roll,
                    options.ready_fail_p,
                    options.max_ready_visits,
                    edges.pass_label,
                    edges.retry_label,
                    edges.fail_label);
            }
            if (edges.labels.size() == 1) {
                return edges.labels[0];
            }
            return std::nullopt;
        };

        ForwardFn forward = [&](const std::string& dest, const TicketJob& job) {
            const auto file = edges.files.find(dest);
            if (file != edges.files.end()) {
                file->second << job.raw_line << '\n';
                file->second.flush();
                return ForwardResult::sent;
            }
            const auto dial = edges.dials.find(dest);
            if (dial != edges.dials.end()) {
                if (!hub.submit(dial->second, job, options.qid)) {
                    return ForwardResult::failed;
                }
                return ForwardResult::sent;
            }
            return ForwardResult::missing;
        };

        auto handle = [&](const TicketJob& job) {
            if (!parse_ticket(job.job_id)) {
                log.error("skipping " + job.job_id + ": job id is not a party ticket");
                return;
            }
            bool found = false;
            for (const auto& task : job.tasks) {
                if (task.tid == tid) {
                    found = true;
                }
            }
            if (!found) {
                log.error("skipping " + job.job_id + ": missing task ids " + std::to_string(tid));
                return;
            }
            visit_job(
                log,
                job,
                options.qid,
                options.server_id,
                tid,
                chooser,
                forward,
                rng,
                state_ptr,
                state_ptr == nullptr ? nullptr : &state_mu);
        };

        if (!options.infile.empty()) {
            std::ifstream input(options.infile);
            if (!input) {
                throw std::runtime_error("cannot open " + options.infile);
            }
            std::string line;
            while (std::getline(input, line)) {
                const auto parsed = parse_job_line(line);
                if (parsed.status == ParseOutcome::Status::skip) {
                    continue;
                }
                if (parsed.status == ParseOutcome::Status::error) {
                    log.error("skipping unreadable job line (" + parsed.error + ")");
                    continue;
                }
                handle(parsed.job);
            }
        } else {
            ListenTarget listen;
            std::string error;
            if (!parse_listen_target(options.ingrpc, &listen, &error)) {
                throw std::runtime_error(error);
            }
            hub.serve_until_interrupt(listen, handle);
        }

        if (state_ptr != nullptr) {
            for (const auto& item : state) {
                log.info("state-visits " + item.first + " " + std::to_string(item.second));
            }
            if (!options.state_file.empty()) {
                save_state(options.state_file, state);
            }
        }
        log.info("Finished");
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << exc.what() << "\n";
        return 1;
    }
}
