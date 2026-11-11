"""Small application operations, independent of FIFO/process setup.

The server validates each job before invoking preparation or delivery. Both
deployment modes use these same operations and forward the original CSV row.
Only the dispatcher owns persistent courier state.
"""

import time

from fd.order import validate_order, WAIT_PRECISION
from lib.events import task_start, task_end, depart_via


def validate_request(job, allowed_restaurants=(0,)):
    """Reject unsupported metadata/task layouts before doing any work."""
    order = validate_order(job)
    if order.restaurant not in allowed_restaurants:
        raise ValueError(
            "unsupported restaurant; allowed restaurant r"
            + ", r".join(map(str, allowed_restaurants))
        )
    return order


def execute_task(job, task, qid, log, sleep):
    """Shared task instrumentation; the supplied duration is already scaled."""
    log.info(
        "%s",
        task_start(job.job_id, qid, task.tid, task.wait_seconds, wait_precision=WAIT_PRECISION),
    )
    sleep(task.wait_seconds)
    log.info(
        "%s", task_end(job.job_id, qid, task.tid, task.wait_seconds, wait_precision=WAIT_PRECISION)
    )


def prepare_order(job, qid, log, sleep=time.sleep):
    """Kitchen operation: perform preparation task 0 only."""
    execute_task(job, job.tasks[0], qid, log, sleep)


def deliver_order(job, qid, log, sleep=time.sleep):
    """Courier operation: perform delivery task 1 only."""
    execute_task(job, job.tasks[1], qid, log, sleep)


def fulfill_order(job, qid, log, sleep=time.sleep):
    """Monolithic baseline: hold one worker through both operations."""
    prepare_order(job, qid, log, sleep)
    deliver_order(job, qid, log, sleep)


def forward_order(job, qid, writer, destination, log):
    """Publish a prepared/completed order; log departure only after flush succeeds.

    This is a handoff for a kitchen worker and completion for a courier or
    monolith. Failure propagates so dispatch cannot release a courier as done.
    """
    writer.write(job.raw_line + "\n")
    writer.flush()
    log.info("%s", depart_via(job.job_id, qid, destination))
