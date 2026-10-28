"""Job-id grammar for a party ticket."""

from __future__ import annotations

import unittest

import support

from mm.ticket import format_ticket_id, parse_ticket

del support


class TicketTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        job_id = format_ticket_id(0, "na", "ranked", "mid", 2)
        ticket = parse_ticket(job_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket.job_id, "t0.na.ranked.mid.p2")
        self.assertEqual(ticket.party, 2)
        self.assertEqual(ticket.band, "mid")

    def test_rejects_a_plain_job_id(self) -> None:
        self.assertIsNone(parse_ticket("j0"))

    def test_rejects_a_party_outside_1_to_5(self) -> None:
        with self.assertRaises(ValueError):
            format_ticket_id(1, "eu", "unrated", "apex", 6)


if __name__ == "__main__":
    unittest.main()
