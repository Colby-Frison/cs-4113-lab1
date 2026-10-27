"""Flag grammar for optional gRPC edges."""

from __future__ import annotations

import unittest

import support  # noqa: F401

from lib.grpc_spec import QUEUE_TO_QUEUE, QUEUE_TO_SERVER, SERVER_TO_QUEUE, parse_dial, parse_listen


class GrpcSpecTests(unittest.TestCase):
    def test_listen_requires_a_port(self) -> None:
        spec = parse_listen("0.0.0.0:50051,5")
        self.assertEqual(spec.address, "0.0.0.0:50051")
        self.assertEqual(spec.workers, 5)
        with self.assertRaises(SystemExit) as raised:
            parse_listen("0.0.0.0,5")
        self.assertIn("port", str(raised.exception).lower())

    def test_queue_dial_defaults_to_the_server_function(self) -> None:
        spec = parse_dial("127.0.0.1:50052,2", caller="queue")
        self.assertEqual(spec.service, QUEUE_TO_SERVER)
        self.assertEqual(spec.workers, 2)
        other = parse_dial("127.0.0.1:50051,1,queue", caller="queue")
        self.assertEqual(other.service, QUEUE_TO_QUEUE)

    def test_server_function_dial_calls_a_queue(self) -> None:
        spec = parse_dial("127.0.0.1:50051,1", caller="server")
        self.assertEqual(spec.service, SERVER_TO_QUEUE)
        with self.assertRaises(SystemExit):
            parse_dial("127.0.0.1:50051,1,server", caller="server")


if __name__ == "__main__":
    unittest.main()
