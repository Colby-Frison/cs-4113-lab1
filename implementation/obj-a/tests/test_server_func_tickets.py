import csv
import io
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

IMPLEMENTATION = Path(__file__).resolve().parents[2]
SOURCE = next(IMPLEMENTATION.rglob("server_func_tickets.py"))

OBJ_A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OBJ_A))

class Ticket:
    def __init__(self, ticket_id, description, priority, status, assigned_team):
        self.ticket_id = ticket_id
        self.description = description
        self.priority = priority
        self.status = status
        self.assigned_team = assigned_team


ticketing = ModuleType("ticketing")
ticketing.Ticket = Ticket

def classify(ticket):
    ticket.priority = "high"
    ticket.status = "classified"

def assign(ticket):
    ticket.assigned_team = "support"
    ticket.status = "assigned"

def resolve(ticket):
    ticket.status = "resolved"

ticketing.classify_ticket = classify
ticketing.assign_ticket = assign
ticketing.resolve_ticket = resolve


class TicketServerTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.root = Path(self.tmp.name)
        self.infile = self.root / "input.csv"
        self.outfile = self.root / "output.csv"

        with self.infile.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "ticket_id", "description", "priority",
                "assigned_team", "status"
            ])
            writer.writeheader()
            writer.writerow({
                "ticket_id": "t1",
                "description": "Test ticket",
                "priority": "low",
                "assigned_team": "",
                "status": "open",
            })

    def run_stage(self, stage, direct=False):
        argv = [
            str(SOURCE),
            "--stage", stage,
            "-i", str(self.infile),
            "-o", str(self.outfile),
            "--qid", "q0",
            "--server-id", "0",
        ]

        with patch.dict(sys.modules, {"ticketing": ticketing}):
            with patch.object(sys, "argv", argv):
                with patch("time.sleep"):
                    with patch("lib.logsetup.configure_logging"):
                        with patch("sys.stdout", new_callable=io.StringIO):
                            module = runpy.run_path(
                                str(SOURCE),
                                run_name="__main__" if direct else "ticket_test"
                            )

                            if not direct:
                                module["main"]()

        with self.outfile.open(newline="") as f:
            return list(csv.DictReader(f))

    def test_classify(self):
        rows = self.run_stage("classify")
        self.assertEqual(rows[0]["priority"], "high")
        self.assertEqual(rows[0]["status"], "classified")

    def test_assign(self):
        rows = self.run_stage("assign")
        self.assertEqual(rows[0]["assigned_team"], "support")
        self.assertEqual(rows[0]["status"], "assigned")

    def test_resolve(self):
        rows = self.run_stage("resolve")
        self.assertEqual(rows[0]["status"], "resolved")

    def test_direct_execution(self):
        rows = self.run_stage("classify", direct=True)
        self.assertEqual(len(rows), 1)

    def test_empty_input(self):
        self.infile.write_text(
            "ticket_id,description,priority,assigned_team,status\n"
        )
        rows = self.run_stage("classify")
        self.assertEqual(rows, [])

    def test_missing_stage(self):
        with patch.dict(sys.modules, {"ticketing": ticketing}):
            module = runpy.run_path(str(SOURCE), run_name="ticket_test")

        with patch.object(sys, "argv", [
            str(SOURCE), "-i", str(self.infile), "-o", str(self.outfile)
        ]):
            with self.assertRaises(SystemExit):
                module["main"]()


if __name__ == "__main__":
    unittest.main()