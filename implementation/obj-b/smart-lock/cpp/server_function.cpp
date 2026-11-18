#include "server_function.hpp"

#include <algorithm>
#include <regex>
#include <stdexcept>

std::string clean_source(const std::string& source) {
    std::string out = source;
    size_t i = 0;

    while (i < source.size()) {
        size_t end = i;

        if (source.compare(i, 2, "//") == 0) {
            end = source.find('\n', i);
            if (end == std::string::npos)
                end = source.size();
        } else if (source.compare(i, 2, "/*") == 0) {
            end = source.find("*/", i + 2);
            end = end == std::string::npos ? source.size() : end + 2;
        } else if (source[i] == '"' || source[i] == '\'') {
            char quote = source[i];
            end = i + 1;

            while (end < source.size()) {
                if (source[end] == '\\') {
                    end = std::min(source.size(), end + 2);
                    continue;
                }
                if (source[end++] == quote)
                    break;
            }
        } else {
            ++i;
            continue;
        }

        for (size_t j = i; j < end; ++j)
            if (out[j] != '\n')
                out[j] = ' ';

        i = end;
    }

    return out;
}

std::vector<Finding> find_candidates(
    const std::string& code,
    const std::string& check
) {
    std::vector<Finding> out;

    const std::regex unlock(R"(\b(unlock|unlockDoor|unlock_door|setUnlocked)\s*\()");
    const std::regex auth(
        R"(\b(auth\w*|\w*permission\w*|verify\w*|isAllowed|is_allowed)\b)",
        std::regex::icase
    );
    const std::regex fresh(
        R"(\b(\w*nonce\w*|\w*sequence\w*|\w*counter\w*|\w*timestamp\w*)\b)",
        std::regex::icase
    );

    auto add = [&](
        const std::smatch& m,
        const std::string& rule,
        const std::string& reason
    ) {
        unsigned line = 1 + std::count(code.begin(), code.begin() + m.position(), '\n');
        out.push_back({rule, line, m.str(1), reason});
    };

    if (check == "input_bounds") {
        const std::regex pattern(R"(\b(gets|strcpy|strcat|sprintf)\s*\()");
        for (std::sregex_iterator it(code.begin(), code.end(), pattern), end; it != end; ++it)
            add(
                *it,
                "unbounded_input_call",
                "Review unbounded input/copy/format call and actual buffer sizes."
            );
    } else if (check == "authorization") {
        for (std::sregex_iterator it(code.begin(), code.end(), unlock), end; it != end; ++it) {
            const auto& m = *it;
            size_t at = m.position();
            size_t begin = at;

            // matching the Python
            int newlines = 0;
            while (begin > 0) {
                --begin;
                if (code[begin] == '\n' && ++newlines == 13) {
                    ++begin;
                    break;
                }
            }

            size_t finish = code.find('\n', at);
            if (finish == std::string::npos)
                finish = code.size();

            if (!std::regex_search(code.substr(begin, finish - begin), auth))
                add(
                    m,
                    "unlock_auth_review",
                    "No nearby authorization identifier; inspect callers and control flow."
                );
        }
    } else if (check == "replay") {
        std::smatch m;
        if (std::regex_search(code, m, unlock) && !std::regex_search(code, fresh))
            add(
                m,
                "unlock_freshness_review",
                "No local freshness identifier; inspect protocol and callers."
            );
    } else
        throw std::runtime_error("unknown check: " + check);

    return out;
}