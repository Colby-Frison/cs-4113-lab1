// Bounded in-process job buffer. Mirrors Python queue.Queue(maxsize=...) with
// put_nowait semantics: a full buffer fails immediately instead of blocking
// the producer. That is what produces ERROR QUEUE-FULL in the logs.

#include "buffer.hpp"

JobBuffer::JobBuffer(std::optional<std::size_t> max_size) : max_size_(max_size) {}

bool JobBuffer::try_push(WaitJob job) {
    std::lock_guard<std::mutex> guard(mu_);
    // Unbounded when max_size_ has no value.
    if (max_size_ && items_.size() >= *max_size_) {
        return false;
    }
    items_.push(std::move(job));
    cv_.notify_one();  // wake one waiting consumer
    return true;
}

std::optional<WaitJob> JobBuffer::take() {
    std::unique_lock<std::mutex> lock(mu_);
    // Sleep until a job or the stop marker is present.
    cv_.wait(lock, [&] { return !items_.empty(); });

    auto item = std::move(items_.front());
    items_.pop();

    if (!item.has_value()) {
        // Stop marker: put it back so every other consumer also sees it.
        // (Python does the same with buffer.put(None) after get() returns None.)
        items_.push(std::nullopt);
        cv_.notify_all();
        return std::nullopt;
    }
    return item;
}

void JobBuffer::push_stop() {
    std::lock_guard<std::mutex> guard(mu_);
    // One shared marker is enough; take() requeues it for the remaining threads.
    items_.push(std::nullopt);
    cv_.notify_all();
}

std::size_t JobBuffer::size() const {
    // Used by the metric-q-size sidecar. May briefly include the stop marker.
    std::lock_guard<std::mutex> guard(mu_);
    return items_.size();
}

bool admit_job(
    JobBuffer& buffer,
    const WaitJob& job,
    const std::string& qid,
    const std::string& source,
    EventLog& log) {
    // Shared path for file producers and gRPC ingress.
    if (!buffer.try_push(job)) {
        // Drop the job. Caller (or gRPC response) learns about the reject via false.
        log.info(EventLog::error_queue_full(job.job_id, qid));
        return false;
    }
    // source becomes the ARRIVE-VIA label (infile path or request.source_edge).
    log.info(EventLog::arrive_via(job.job_id, qid, source));
    return true;
}
