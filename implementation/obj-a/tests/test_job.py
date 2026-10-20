"""Job CSV format and parser."""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts implementation/obj-a on sys.path)

from lib.job import HEADER, JobParseError, Task, format_job_line, iter_jobs, parse_job_line


class JobLineTests(unittest.TestCase):
    def test_round_trip_matches_lab0_quoting(self) -> None:
        tasks = (Task(0, 0.25), Task(1, 1.0))
        line = format_job_line("j0", 1760990100.123, tasks)
        self.assertEqual(
            line,
            'j0,1760990100.123,"[""0: W 0.250000"", ""1: W 1.000000""]"',
        )
        job = parse_job_line(line)
        self.assertEqual(job.raw_line, line)
        self.assertEqual(job.job_id, "j0")
        self.assertEqual(job.arrival_time, 1760990100.123)
        self.assertEqual(job.tasks, tasks)

    def test_header_is_skipped(self) -> None:
        line = format_job_line("j1", 2.5, (Task(0, 0.5),))
        jobs = list(iter_jobs([HEADER + "\n", "\n", line + "\n"]))
        self.assertEqual([job.job_id for job in jobs], ["j1"])

    def test_bad_lines_are_rejected(self) -> None:
        samples = [
            "",
            HEADER,
            "only,two",
            "j0,not-a-time,\"[]\"",
            'j0,-1.0,"[""0: W 1.000000""]"',
            'j0,1.0,"[]"',
            'j0,1.0,"[""nope""]"',
        ]
        for line in samples:
            with self.assertRaises(JobParseError, msg=line):
                parse_job_line(line)


if __name__ == "__main__":
    unittest.main()
