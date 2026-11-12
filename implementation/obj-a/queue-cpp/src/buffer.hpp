#pragma once

#include "events.hpp"
#include "job.hpp"

#include <condition_variable>
#include <cstddef>
#include <mutex>
#include <optional>
#include <queue>

// Thread-safe bounded job buffer for one queue process.
//
// try_push never blocks: a full buffer returns false so the caller can log
// ERROR QUEUE-FULL and drop the job (Python's Queue.put_nowait).
//
// Shutdown uses one shared stop marker (nullopt). take() requeues that marker
// so every consumer thread sees it and exits — same trick as Python's None.

class JobBuffer {
public:
    // max_size unset => unbounded. Otherwise capacity is that many waiting jobs.
    explicit JobBuffer(std::optional<std::size_t> max_size = std::nullopt);

    bool try_push(WaitJob job);           // false when at capacity
    std::optional<WaitJob> take();        // nullopt => stop; blocks while empty
    void push_stop();
    std::size_t size() const;             // includes a stop marker if present

private:
    mutable std::mutex mu_;
    std::condition_variable cv_;
    std::queue<std::optional<WaitJob>> items_;
    std::optional<std::size_t> max_size_;
};

// Admit into the buffer and log ARRIVE-VIA, or log QUEUE-FULL and return false.
bool admit_job(
    JobBuffer& buffer,
    const WaitJob& job,
    const std::string& qid,
    const std::string& source,
    EventLog& log);
