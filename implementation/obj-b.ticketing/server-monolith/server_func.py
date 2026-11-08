
import argparse
import csv
import logging
import sys
import threading
import time
from pathlib import Path

# Set up imports
APP_DIR = Path(__file__).resolve().parents[1]
OBJ_A_DIR = APP_DIR.parent / "obj-a"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(OBJ_A_DIR))

from ticketing import (
    Ticket,
    classify_ticket,
    assign_ticket,
    resolve_ticket,
)

from lib.job import iter_jobs
from lib.events import (
    enters_server,
    task_start,
    task_end,
    exits_server,
    depart_via,
)
from lib.logsetup import configure_logging
from lib.grpc_spec import parse_listen


def process_job(job, writer, destination, write_lock, logger, args):
    """Process one ticket and save the result."""

    job_id = job.job_id

    # The workload generator stores the description in the job ID.
    if "_" in job_id:
        description = job_id.split("_", 1)[1].replace("_", " ")
    else:
        description = job_id

    ticket = Ticket(
        ticket_id=job_id,
        description=description,
    )

    logger.info(
        "%s",
        enters_server(job_id, args.qid, args.server_id),
    )

    stages = [
        classify_ticket,
        assign_ticket,
        resolve_ticket,
    ]

    # Process classification, assignment, and resolution.
    for stage, task in zip(stages, job.tasks):
        logger.info(
            "%s",
            task_start(
                job_id,
                args.qid,
                task.tid,
                task.wait_seconds,
            ),
        )

        stage(ticket)

        # Use the workload-defined processing duration.
        time.sleep(task.wait_seconds)

        logger.info(
            "%s",
            task_end(
                job_id,
                args.qid,
                task.tid,
                task.wait_seconds,
            ),
        )

    logger.info(
        "%s",
        exits_server(job_id, args.qid, args.server_id),
    )

    # Protect shared CSV output from concurrent gRPC workers.
    with write_lock:
        writer.writerow([
            ticket.ticket_id,
            ticket.description,
            ticket.priority,
            ticket.assigned_team,
            ticket.status,
        ])
        destination.flush()

    logger.info(
        "%s",
        depart_via(job_id, args.qid, args.outfile),
    )

    print(
        f"{ticket.ticket_id}: "
        f"{ticket.assigned_team} -> {ticket.status}",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Customer support ticket processing server"
    )

    parser.add_argument(
        "-i",
        "--infile",
        default=None,
        help="Input job CSV or FIFO",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        required=True,
        help="Output CSV for resolved tickets",
    )
    parser.add_argument("--logfile", default=None)
    parser.add_argument("--qid", default="q0")
    parser.add_argument("--server-id", type=int, default=0)
    parser.add_argument(
        "--ingrpc",
        default=None,
        help="Listen for incoming jobs: interface:port,workers",
    )

    args = parser.parse_args()

    # Exactly one input method must be selected.
    if bool(args.infile) == bool(args.ingrpc):
        parser.error(
            "Provide either --infile or --ingrpc, but not both."
        )

    if args.server_id < 0:
        parser.error("--server-id must be >= 0")

    output = Path(args.outfile)
    output.parent.mkdir(parents=True, exist_ok=True)

    configure_logging(args.logfile, "sv")
    logger = logging.getLogger("server-func")

    logger.info("Started")
    logger.info(
        "qid %s server-id %s",
        args.qid,
        args.server_id,
    )

    write_lock = threading.Lock()

    # Open the output once for either CSV or gRPC mode.
    with output.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as destination:

        writer = csv.writer(destination)

        writer.writerow([
            "ticket_id",
            "description",
            "priority",
            "assigned_team",
            "status",
        ])
        destination.flush()

        def handle_job(job):
            process_job(
                job,
                writer,
                destination,
                write_lock,
                logger,
                args,
            )

        if args.infile:
            # CSV or FIFO input mode.
            logger.info("Reading jobs from %s", args.infile)

            with open(
                args.infile,
                encoding="utf-8",
            ) as source:
                for job in iter_jobs(source):
                    handle_job(job)

        else:
            # gRPC input mode.
            # Import only when gRPC is actually needed.
            from lib.grpc_runtime import start_server_func_server

            listen = parse_listen(args.ingrpc)

            server, port = start_server_func_server(
                listen,
                handle_job,
            )

            logger.info(
                "gRPC listen %s:%s workers %s",
                listen.host,
                port,
                listen.workers,
            )

            print(
                f"Ticketing gRPC server listening on "
                f"{listen.host}:{port}",
                flush=True,
            )

            try:
                while True:
                    time.sleep(0.2)
            except KeyboardInterrupt:
                logger.info("Stopping gRPC server")
            finally:
                server.stop(1).wait()

    logger.info("Finished")
    print(
        f"Finished processing tickets. Results saved to {output}"
    )


if __name__ == "__main__":
    main()
