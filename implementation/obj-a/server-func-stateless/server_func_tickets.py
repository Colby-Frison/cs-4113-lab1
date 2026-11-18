
import argparse
import csv
import logging
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
OBJ_A_DIR = APP_DIR.parent / "obj-a"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(OBJ_A_DIR))

from ticketing import Ticket, classify_ticket, assign_ticket, resolve_ticket
from lib.job import iter_jobs
from lib.events import (
    enters_server,
    task_start,
    task_end,
    exits_server,
    depart_via,
)
from lib.logsetup import configure_logging


STAGES = {
    "classify": (classify_ticket, 1),
    "assign": (assign_ticket, 2),
    "resolve": (resolve_ticket, 3),
}

FIELDS = [
    "ticket_id",
    "description",
    "priority",
    "assigned_team",
    "status",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("-i", "--infile", required=True)
    parser.add_argument("-o", "--outfile", required=True)
    parser.add_argument("--logfile")
    parser.add_argument("--qid", default="q0")
    parser.add_argument("--server-id", type=int, default=0)
    args = parser.parse_args()

    configure_logging(args.logfile, "sv")
    logger = logging.getLogger("server-func")

    stage_fn, task_id = STAGES[args.stage]

    output = Path(args.outfile)
    output.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Started")
    logger.info("qid %s server-id %s", args.qid, args.server_id)

    with open(args.infile, newline="", encoding="utf-8") as source, \
         output.open("w", newline="", encoding="utf-8") as destination:

        reader = csv.DictReader(source)
        writer = csv.DictWriter(destination, fieldnames=FIELDS)
        writer.writeheader()

        for row in reader:
            ticket = Ticket(
                ticket_id=row["ticket_id"],
                description=row["description"],
                priority=row["priority"],
                status=row["status"],
                assigned_team=row["assigned_team"],
            )

            logger.info(
                "%s",
                enters_server(ticket.ticket_id, args.qid, args.server_id),
            )

            logger.info(
                "%s",
                task_start(ticket.ticket_id, args.qid, task_id, 0.01),
            )

            stage_fn(ticket)
            time.sleep(0.01)

            logger.info(
                "%s",
                task_end(ticket.ticket_id, args.qid, task_id, 0.01),
            )

            logger.info(
                "%s",
                exits_server(ticket.ticket_id, args.qid, args.server_id),
            )

            writer.writerow({
                "ticket_id": ticket.ticket_id,
                "description": ticket.description,
                "priority": ticket.priority,
                "assigned_team": ticket.assigned_team,
                "status": ticket.status,
            })
            destination.flush()

            logger.info(
                "%s",
                depart_via(ticket.ticket_id, args.qid, str(output)),
            )

            print(
                f"{args.stage}: {ticket.ticket_id} -> {ticket.status}"
            )

    logger.info("Finished")
    print(f"Finished {args.stage} stage. Output: {output}")


if __name__ == "__main__":
    main()

