
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ticketing import Ticket, classify_ticket, assign_ticket, resolve_ticket, process_ticket


class TestTicketing(unittest.TestCase):

    def test_account_support(self):
        ticket = Ticket("T001", "I cannot login to my account")
        result = process_ticket(ticket)

        self.assertEqual(result.assigned_team, "account_support")
        self.assertEqual(result.status, "resolved")

    def test_billing_support(self):
        ticket = Ticket("T002", "I have a billing problem")
        result = process_ticket(ticket)

        self.assertEqual(result.assigned_team, "billing_support")
        self.assertEqual(result.status, "resolved")

    def test_general_support(self):
        ticket = Ticket("T003", "My application is not working")
        result = process_ticket(ticket)

        self.assertEqual(result.assigned_team, "general_support")
        self.assertEqual(result.status, "resolved")

    def test_assignment_requires_classification(self):
        ticket = Ticket("T004", "I need help")

        with self.assertRaises(ValueError):
            assign_ticket(ticket)

    def test_resolution_requires_assignment(self):
        ticket = Ticket("T005", "I need help")
        classify_ticket(ticket)

        with self.assertRaises(ValueError):
            resolve_ticket(ticket)


if __name__ == "__main__":
    unittest.main()
