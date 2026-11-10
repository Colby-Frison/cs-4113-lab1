#pragma once
#include <string>
#include <vector>
struct Finding { std::string rule; unsigned line; std::string symbol, reason; };
std::string clean_source(const std::string& source);
std::vector<Finding> find_candidates(const std::string& code,const std::string& check);
