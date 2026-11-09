"""Port layout and job partitioning for N shards x M replicas."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lib.job import is_header, parse_job_line
from lib.kv_routing import partition_lines, shard_index


@dataclass(frozen=True)
class ShardPlan:
    shard: int
    sf_port: int
    replica_ports: tuple[int, ...]
    jobs_csv: Path
    queued_csv: Path
    sink_csv: Path
    kv_json: Path
    replica_json: tuple[Path, ...]
    q_log: Path
    sf_log: Path
    replica_logs: tuple[Path, ...]


@dataclass(frozen=True)
class Topology:
    shards: int
    replicas: int
    plans: tuple[ShardPlan, ...]

    @property
    def uses_2pc(self) -> bool:
        return self.replicas >= 2


def build_topology(
    run_dir: Path,
    *,
    shards: int,
    replicas: int,
    sf_port0: int = 27000,
    replica_port0: int = 28000,
) -> Topology:
    """Assign ports and paths under ``run_dir`` for one (N, M) configuration."""
    if shards < 1:
        raise ValueError("shards must be >= 1")
    if replicas < 1:
        raise ValueError("replicas must be >= 1")
    plans: list[ShardPlan] = []
    for shard in range(shards):
        replica_ports = tuple(
            replica_port0 + shard * max(replicas, 1) + index for index in range(replicas)
        )
        replica_json = tuple(run_dir / f"kv-shard-{shard}-r{index}.json" for index in range(replicas))
        replica_logs = tuple(run_dir / f"replica-s{shard}-r{index}.log" for index in range(replicas))
        plans.append(
            ShardPlan(
                shard=shard,
                sf_port=sf_port0 + shard,
                replica_ports=replica_ports if replicas >= 2 else (),
                jobs_csv=run_dir / f"jobs-s{shard}.csv",
                queued_csv=run_dir / f"queued-s{shard}.csv",
                sink_csv=run_dir / f"sink-s{shard}.csv",
                kv_json=run_dir / f"kv-shard-{shard}.json",
                replica_json=replica_json if replicas >= 2 else (),
                q_log=run_dir / f"q-s{shard}.log",
                sf_log=run_dir / f"sf-s{shard}.log",
                replica_logs=replica_logs if replicas >= 2 else (),
            )
        )
    return Topology(shards=shards, replicas=replicas, plans=tuple(plans))


def write_partitioned_jobs(source_csv: Path, topology: Topology) -> None:
    """Split ``source_csv`` into per-shard job files by ``shard_index(job_id, N)``."""
    lines = source_csv.read_text(encoding="utf-8").splitlines()
    header = ""
    body: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        if not header and is_header(line):
            header = line
            continue
        body.append(line)
    buckets = partition_lines(
        body,
        topology.shards,
        key_from_line=lambda line: parse_job_line(line).job_id,
    )
    for plan, bucket in zip(topology.plans, buckets, strict=True):
        parts = [header] if header else []
        parts.extend(bucket)
        plan.jobs_csv.write_text("\n".join(parts) + ("\n" if parts else ""), encoding="utf-8")


def expected_shard_for_job(job_id: str, shards: int) -> int:
    return shard_index(job_id, shards)
