"""Stage-specific or monolithic service; each process is one worker resource."""

import argparse
import logging
import os
import time

from lib.events import enters_server, exits_server
from lib.job import iter_jobs
from lib.logsetup import configure_logging
from fd.services import validate_request, prepare_order, deliver_order, fulfill_order, forward_order

SERVICES = {"kitchen": prepare_order, "courier": deliver_order, "monolith": fulfill_order}


def serve(job, stage, qid, worker_id, writer, destination, log, sleep=time.sleep):
    service = SERVICES[stage]
    validate_request(job)
    log.info("%s", enters_server(job.job_id, qid, worker_id))
    if stage in ("courier", "monolith"):
        log.info("courier-assigned %s %s", worker_id, job.job_id)
    service(job, qid, log, sleep)
    log.info("%s", exits_server(job.job_id, qid, worker_id))
    forward_order(job, qid, writer, destination, log)
    if stage in ("courier", "monolith"):
        log.info("courier-released %s %s", worker_id, job.job_id)


def main(default_stage=None):
    parser = argparse.ArgumentParser(description=__doc__)
    if default_stage is None:
        parser.add_argument("--stage", choices=("kitchen", "courier"), required=True)
    parser.add_argument("-i", "--infile", required=True)
    parser.add_argument("-o", "--outfile", required=True)
    parser.add_argument("--qid", required=True)
    parser.add_argument("--server-id", type=int, default=0)
    parser.add_argument("--logfile")
    args = parser.parse_args()
    if args.server_id < 0:
        parser.error("server-id must be nonnegative")
    os.environ["LAB_LOG_COMPACT"] = "1"
    configure_logging(args.logfile, "sv")
    log = logging.getLogger("food-delivery")
    with (
        open(args.infile, encoding="utf-8") as source,
        open(args.outfile, "a", encoding="utf-8") as dest,
    ):
        for job in iter_jobs(source):
            serve(
                job, default_stage or args.stage, args.qid, args.server_id, dest, args.outfile, log
            )
