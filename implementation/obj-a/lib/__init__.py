"""Shared pieces for the Objective A queue framework.

Scripts under ``workload-generator/``, ``queue/``, and ``server-func-*/`` insert
this directory on ``sys.path`` and import ``lib``. They do not install a package.
``job`` owns the CSV row, ``events`` owns the Lab 0 message body, and
``server`` runs one wait-task visit. ``grpc_spec`` parses flags with no grpc
import; ``grpc_runtime`` is imported only when a process was given an RPC edge.
``metrics`` exposes optional Prometheus ``/metrics`` when ``--metrics-port`` is set.
"""
