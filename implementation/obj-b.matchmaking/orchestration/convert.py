"""Convert a matchmaking topology into Compose and Kubernetes manifests.

The topology names queues, stages, worker pools, and sink files. Ports, FIFO
paths, and process arguments are assigned here so the same JSON can be
launched with gRPC or with FIFOs, and applied to Kubernetes after that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PYTHON = "python"
QUEUE_SCRIPT = "/app/obj-a/queue/queue.py"
STAGE_SCRIPT = "/app/obj-b.matchmaking/server-stages/server_func.py"
GENERATOR_SCRIPT = "/app/obj-b.matchmaking/workload-generator/workload_generator.py"
HEALTHCHECK = "/app/obj-b.matchmaking/orchestration/healthcheck.py"
SHARED = "/shared"
FIFO_ROOT = "/fifos"
JOBS_FIFO = f"{FIFO_ROOT}/jobs"
READY_FLAG = f"{FIFO_ROOT}/admit.ready"
STATE_FILE = f"{SHARED}/ready-state.txt"
PORT_BASE = 5001
METRICS_PORT = 9100
METRICS_HOST_BASE = 9100
IMAGE = "matchmaking:local"
HOLD = "fifo-hold"
GENERATOR = "generator"
RESERVED = {HOLD, GENERATOR}

MM_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOPOLOGY = MM_ROOT / "topology" / "baseline.json"
GENERATED = Path(__file__).resolve().parent / "generated"


@dataclass
class Service:
    """One container. ``depends_on`` is other service names, all health-gated."""

    name: str
    command: list[str]
    listen_port: int | None = None
    metrics_port: int | None = None
    metrics_host_port: int | None = None
    depends_on: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    mount_fifos: bool = False
    mount_shared: bool = False
    restart: str = "unless-stopped"
    health: str = "none"


@dataclass
class Plan:
    """Manifests for one edge type. ``count`` is None when the generator streams."""

    edge: str
    project: str
    services: list[Service]
    fifos: list[str]
    sink_files: dict[str, str]
    count: int | None
    runs_mount: str


def load_topology(path: Path | None = None) -> dict:
    source = DEFAULT_TOPOLOGY if path is None else path
    return json.loads(source.read_text(encoding="utf-8"))


def build_plan(topology: dict, *, edge: str, continuous: bool = False) -> Plan:
    """Build the container list for ``grpc`` or ``fifo`` edges."""
    if edge not in ("grpc", "fifo"):
        raise SystemExit("edge must be grpc or fifo")
    pipeline = topology.get("pipeline")
    if not isinstance(pipeline, list) or not pipeline:
        raise SystemExit("topology needs a non-empty pipeline")
    sinks = dict(topology.get("sinks") or {})
    generator = dict(topology.get("generator") or {})
    steps = [_read_step(item, index, sinks) for index, item in enumerate(pipeline)]
    _check_names(steps)
    ports = _assign_ports(steps) if edge == "grpc" else {}
    fifos = _fifo_paths(steps, edge)
    services = [_hold_service(fifos)]
    services.extend(_work_services(steps, edge, ports, topology, generator))
    services.append(_generator_service(generator, continuous))
    _wire_dependencies(services, edge, steps[0].queue_id)
    _require_acyclic(services)
    count = None if continuous else generator.get("count")
    if count is not None:
        count = int(count)
    return Plan(
        edge=edge,
        project=f"matchmaking-{edge}",
        services=services,
        fifos=fifos,
        sink_files={key: _sink_path(sinks, key) for key in sinks},
        count=count,
        runs_mount=f"../runs/{edge}",
    )


def write_outputs(topology: dict, directory: Path, *, continuous: bool = False) -> list[Path]:
    """Write both Compose files and the refined gRPC Kubernetes manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    grpc_plan = None
    for edge in ("grpc", "fifo"):
        plan = build_plan(topology, edge=edge, continuous=continuous)
        if edge == "grpc":
            grpc_plan = plan
        path = directory / f"compose.{edge}.yml"
        path.write_text(render_compose(plan), encoding="utf-8", newline="\n")
        written.append(path)
    if grpc_plan is None:
        raise SystemExit("grpc plan was not built")
    k8s = directory / "k8s.yaml"
    k8s.write_text(render_k8s(grpc_plan), encoding="utf-8", newline="\n")
    written.append(k8s)
    return written


def render_compose(plan: Plan) -> str:
    """Docker Compose file. The build context is ``implementation/``."""
    lines = [
        f"# Generated from the matchmaking topology for {plan.edge} edges.",
        "# Rebuild with orchestration/run.py --write-only.",
        f"name: {plan.project}",
        "x-image: &image",
        f"  image: {IMAGE}",
        "  pull_policy: never",
        "services:",
    ]
    for service in plan.services:
        lines.append(f"  {service.name}:")
        lines.append("    <<: *image")
        if service.name == HOLD:
            # One service owns the build. Parallel exports of the same tag fail.
            lines.extend(
                [
                    "    build:",
                    "      context: ../../..",
                    "      dockerfile: obj-b.matchmaking/orchestration/Dockerfile",
                ]
            )
        if service.command:
            lines.append("    command:")
            for arg in service.command:
                lines.append(f"      - {_yaml(arg)}")
        if service.environment:
            lines.append("    environment:")
            for key in sorted(service.environment):
                lines.append(f"      {key}: {_yaml(service.environment[key])}")
        if service.depends_on:
            lines.append("    depends_on:")
            for name in service.depends_on:
                lines.append(f"      {name}:")
                lines.append("        condition: service_healthy")
        mounts = _compose_mounts(service, plan.runs_mount)
        if mounts:
            lines.append("    volumes:")
            for mount in mounts:
                lines.append(f"      - {_yaml(mount)}")
        if service.metrics_port is not None and service.metrics_host_port is not None:
            lines.extend(
                [
                    "    ports:",
                    f"      - {_yaml(f'{service.metrics_host_port}:{service.metrics_port}')}",
                ]
            )
        if service.health != "none":
            lines.extend(
                [
                    "    healthcheck:",
                    "      test:",
                    "        - CMD",
                    "        - python",
                    f"        - {_yaml(HEALTHCHECK)}",
                    "      interval: 2s",
                    "      timeout: 3s",
                    "      retries: 40",
                    "      start_period: 3s",
                ]
            )
        lines.append(f"    restart: {_yaml(service.restart)}")
    lines.extend(["volumes:", "  fifos:", ""])
    return "\n".join(lines)


def render_k8s(plan: Plan) -> str:
    """Refined Kubernetes manifest for the gRPC plan.

    Kompose can translate the Compose file mechanically. This manifest is the
    refined application: one namespace, a Service and TCP probes per listener,
    resource requests, a FIFO volume shared by the admit queue and the
    generator, a results volume for the sinks, and a Job for a finite run.
    """
    if plan.edge != "grpc":
        raise SystemExit("kubernetes manifests are built from the grpc plan")
    documents = [_namespace(), _pvc("matchmaking-fifos"), _pvc("matchmaking-results")]
    for service in plan.services:
        if service.name == GENERATOR and plan.count is not None:
            documents.append(_job(service, plan))
        else:
            documents.append(_deployment(service))
        if service.listen_port is not None:
            documents.append(_service(service))
    return "\n".join(documents)


@dataclass
class _Step:
    queue_id: str
    qsize: int
    queue_workers: int
    stage: str
    server_id: int
    server_workers: int
    dests: list[_Dest]


@dataclass
class _Dest:
    kind: str
    name: str
    role: str
    region: str | None = None

    @property
    def service(self) -> str:
        if self.kind == "queue":
            return self.name
        raise RuntimeError("file destinations have no service")


def _read_step(item: dict, index: int, sinks: dict) -> _Step:
    try:
        queue = item["queue"]
        server = item["server"]
        step = _Step(
            queue_id=str(queue["id"]),
            qsize=int(queue["qsize"]),
            queue_workers=int(queue["workers"]),
            stage=str(server["stage"]),
            server_id=int(server["server_id"]),
            server_workers=int(server["workers"]),
            dests=[],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"pipeline step {index} is missing queue or server fields") from exc
    if step.qsize < 1 or step.queue_workers < 1 or step.server_workers < 1:
        raise SystemExit(f"{step.queue_id} sizes and worker counts must be >= 1")
    step.dests = _destinations(step, server, sinks)
    return step


def _destinations(step: _Step, server: dict, sinks: dict) -> list[_Dest]:
    if step.stage == "route":
        routes = server.get("routes") or {}
        if not routes:
            raise SystemExit("--stage route requires routes")
        return [_Dest("queue", str(qid), "forward", str(region)) for region, qid in routes.items()]
    if step.stage == "ready":
        for role in ("pass", "retry", "fail"):
            if role not in server:
                raise SystemExit("ready stage requires pass, retry, and fail")
        return [
            _Dest("queue", str(server["pass"]), "pass"),
            _Dest("queue", str(server["retry"]), "retry"),
            _Dest("file", _sink_path(sinks, str(server["fail"])), "fail"),
        ]
    if step.stage == "allocate":
        if "sink" not in server:
            raise SystemExit("allocate stage requires sink")
        return [_Dest("file", _sink_path(sinks, str(server["sink"])), "forward")]
    return [_Dest("queue", "", "forward")]


def _check_names(steps: list[_Step]) -> None:
    names = [HOLD, GENERATOR]
    for step in steps:
        sid = f"s-{step.stage}"
        for name in (step.queue_id, sid):
            if name in names or name in RESERVED:
                raise SystemExit(f"service name {name} is already used")
            names.append(name)
        known = {item.queue_id for item in steps}
        for dest in step.dests:
            if dest.kind == "queue" and dest.name and dest.name not in known:
                raise SystemExit(f"{step.stage} sends to unknown queue {dest.name}")


def _assign_ports(steps: list[_Step]) -> dict[str, int]:
    """One listen port per process that receives gRPC. The file-fed queue has none."""
    _fill_next_queues(steps)
    targets = {dest.name for step in steps for dest in step.dests if dest.kind == "queue"}
    ports: dict[str, int] = {}
    port = PORT_BASE
    for step in steps:
        if step.queue_id in targets:
            ports[step.queue_id] = port
            port += 1
        ports[f"s-{step.stage}"] = port
        port += 1
    return ports


def _fill_next_queues(steps: list[_Step]) -> None:
    known = {step.queue_id for step in steps}
    for index, step in enumerate(steps):
        for dest in step.dests:
            if dest.kind == "queue" and not dest.name:
                if index + 1 >= len(steps):
                    raise SystemExit(f"{step.stage} has no downstream queue")
                dest.name = steps[index + 1].queue_id
            if dest.kind == "queue" and dest.name not in known:
                raise SystemExit(f"{step.stage} sends to unknown queue {dest.name}")


def _fifo_paths(steps: list[_Step], edge: str) -> list[str]:
    _fill_next_queues(steps)
    paths = [JOBS_FIFO]
    if edge != "fifo":
        return paths
    for step in steps:
        paths.append(_fifo(step.queue_id, f"s-{step.stage}"))
        for dest in step.dests:
            if dest.kind == "queue":
                paths.append(_fifo(f"s-{step.stage}", dest.name))
    return paths


def _fifo(src: str, dest: str) -> str:
    return f"{FIFO_ROOT}/{src}-to-{dest}"


def _hold_service(fifos: list[str]) -> Service:
    return Service(
        name=HOLD,
        command=[PYTHON, "-c", "import time; time.sleep(10**9)"],
        environment={
            "HOLD_FIFOS": ",".join(fifos),
            "CLEAR_FILES": READY_FLAG,
            "HEALTH_EXIST": ",".join(fifos),
        },
        mount_fifos=True,
        health="exist",
    )


def _work_services(
    steps: list[_Step],
    edge: str,
    ports: dict[str, int],
    topology: dict,
    generator: dict,
) -> list[Service]:
    _fill_next_queues(steps)
    services: list[Service] = []
    metrics_index = 0
    for index, step in enumerate(steps):
        services.append(_queue_service(steps, index, edge, ports, metrics_index))
        metrics_index += 1
        services.append(_server_service(step, edge, ports, topology, generator, metrics_index))
        metrics_index += 1
    return services


def _queue_service(
    steps: list[_Step],
    index: int,
    edge: str,
    ports: dict[str, int],
    metrics_index: int,
) -> Service:
    step = steps[index]
    sid = f"s-{step.stage}"
    command = [
        PYTHON,
        QUEUE_SCRIPT,
        "--qid",
        step.queue_id,
        "--qsize",
        str(step.qsize),
        "--metrics-port",
        str(METRICS_PORT),
    ]
    incoming = _queue_inputs(steps, index, edge)
    for path in incoming:
        command.extend(["-i", path])
    listen = ports.get(step.queue_id) if edge == "grpc" else None
    if listen is not None:
        command.extend(["--ingrpc", f"0.0.0.0:{listen},{step.queue_workers}"])
    if edge == "grpc":
        command.extend(["--outgrpc", f"{sid}:{ports[sid]},1"])
    else:
        command.extend(["-o", _fifo(step.queue_id, sid)])
    waits = [f"{sid}:{ports[sid]}"] if edge == "grpc" else []
    environment: dict[str, str] = {}
    health = "needle"
    if index == 0:
        environment["SIGNAL_FIFO"] = JOBS_FIFO
        environment["READY_FILE"] = READY_FLAG
        environment["HEALTH_READY"] = READY_FLAG
        health = "ready"
    elif listen is not None:
        environment["HEALTH_TCP"] = str(listen)
        health = "tcp"
    else:
        environment["HEALTH_NEEDLE"] = "queue.py"
    if waits:
        environment["WAIT_FOR"] = ",".join(waits)
    return Service(
        name=step.queue_id,
        command=command,
        listen_port=listen,
        metrics_port=METRICS_PORT,
        metrics_host_port=METRICS_HOST_BASE + metrics_index,
        environment=environment,
        mount_fifos=bool(incoming) or edge == "fifo",
        health=health,
    )


def _queue_inputs(steps: list[_Step], index: int, edge: str) -> list[str]:
    step = steps[index]
    incoming: list[str] = []
    if index == 0:
        incoming.append(JOBS_FIFO)
    if edge != "fifo":
        return incoming
    for other in steps:
        for dest in other.dests:
            if dest.kind == "queue" and dest.name == step.queue_id:
                incoming.append(_fifo(f"s-{other.stage}", step.queue_id))
    return incoming


def _server_service(
    step: _Step,
    edge: str,
    ports: dict[str, int],
    topology: dict,
    generator: dict,
    metrics_index: int,
) -> Service:
    sid = f"s-{step.stage}"
    command = [
        PYTHON,
        STAGE_SCRIPT,
        "--stage",
        step.stage,
        "--qid",
        step.queue_id,
        "--server-id",
        str(step.server_id),
        "--metrics-port",
        str(METRICS_PORT),
    ]
    listen = ports.get(sid) if edge == "grpc" else None
    environment: dict[str, str] = {}
    if listen is not None:
        command.extend(["--ingrpc", f"0.0.0.0:{listen},{step.server_workers}"])
        environment["HEALTH_TCP"] = str(listen)
        health = "tcp"
    else:
        command.extend(["--infile", _fifo(step.queue_id, sid)])
        environment["HEALTH_NEEDLE"] = "server_func.py"
        health = "needle"
    waits: list[str] = []
    shared = False
    for dest in step.dests:
        token, used_port = _dest_token(step, dest, edge, ports)
        shared = shared or token.startswith(SHARED)
        if used_port is not None and not (step.stage == "ready" and dest.role == "retry"):
            waits.append(f"{dest.service}:{used_port}")
        if step.stage == "route":
            command.extend(["--route-edge", f"{dest.region}={token}"])
        elif step.stage == "ready" and dest.role == "pass":
            command.extend(["--pass", token])
        elif step.stage == "ready" and dest.role == "retry":
            command.extend(["--retry", token])
        elif step.stage == "ready" and dest.role == "fail":
            command.extend(["--fail", token])
        else:
            flag = "--outfile" if dest.kind == "file" or edge == "fifo" else "--outgrpc"
            if dest.kind == "queue" and edge == "grpc":
                flag = "--outgrpc"
            command.extend([flag, token])
    if step.stage == "ready":
        command.extend(
            [
                "--ready-fail-p",
                str(topology.get("ready_fail_p", 0.2)),
                "--max-ready-visits",
                str(topology.get("max_ready_visits", 2)),
                "--state-file",
                STATE_FILE,
            ]
        )
        seed = generator.get("seed")
        if seed is not None:
            command.extend(["--seed", str(seed)])
        shared = True
    if waits:
        environment["WAIT_FOR"] = ",".join(waits)
    return Service(
        name=sid,
        command=command,
        listen_port=listen,
        metrics_port=METRICS_PORT,
        metrics_host_port=METRICS_HOST_BASE + metrics_index,
        environment=environment,
        mount_fifos=edge == "fifo",
        mount_shared=shared,
        health=health,
    )


def _dest_token(step: _Step, dest: _Dest, edge: str, ports: dict[str, int]) -> tuple[str, int | None]:
    if dest.kind == "file":
        return dest.name, None
    if edge == "fifo":
        return _fifo(f"s-{step.stage}", dest.name), None
    port = ports[dest.name]
    return f"{dest.name}:{port},1", port


def _generator_service(generator: dict, continuous: bool) -> Service:
    command = [PYTHON, GENERATOR_SCRIPT, "--out", JOBS_FIFO]
    if not continuous and generator.get("count") is not None:
        command.extend(["--count", str(int(generator["count"]))])
    for flag, key in (
        ("--seed", "seed"),
        ("--time-scale", "time_scale"),
        ("--arrival-mean", "arrival_mean"),
        ("--region", "region"),
        ("--mode", "mode"),
    ):
        if generator.get(key) is not None:
            command.extend([flag, str(generator[key])])
    return Service(
        name=GENERATOR,
        command=command,
        environment={"WAIT_PATH": READY_FLAG},
        mount_fifos=True,
        restart="no",
        health="none",
    )


def _wire_dependencies(services: list[Service], edge: str, first_queue: str) -> None:
    by_name = {service.name: service for service in services}
    work = [service.name for service in services if service.name not in (HOLD, GENERATOR)]
    for service in services:
        deps: list[str] = []
        if service.mount_fifos and service.name != HOLD:
            deps.append(HOLD)
        wait = service.environment.get("WAIT_FOR", "")
        for spec in filter(None, wait.split(",")):
            host = spec.rsplit(":", 1)[0]
            if host in by_name and host not in deps:
                deps.append(host)
        if service.name == GENERATOR:
            extra = [first_queue] if edge == "grpc" else work
            for name in extra:
                if name not in deps:
                    deps.append(name)
        service.depends_on = deps


def _require_acyclic(services: list[Service]) -> None:
    graph = {service.name: list(service.depends_on) for service in services}
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(name: str) -> None:
        if name in visiting:
            raise SystemExit(f"dependency cycle at {name}")
        if name in visited:
            return
        visiting.add(name)
        for dep in graph.get(name, ()):
            walk(dep)
        visiting.remove(name)
        visited.add(name)

    for name in graph:
        walk(name)


def _sink_path(sinks: dict, key: str) -> str:
    try:
        name = str(sinks[key])
    except KeyError as exc:
        raise SystemExit(f"sink {key!r} is not listed in topology sinks") from exc
    if "/" in name or "\\" in name or not name:
        raise SystemExit(f"sink {key} must be a file name")
    return f"{SHARED}/{name}"


def _compose_mounts(service: Service, runs_mount: str) -> list[str]:
    mounts = []
    if service.mount_fifos:
        mounts.append("fifos:/fifos")
    if service.mount_shared:
        mounts.append(f"{runs_mount}:/shared")
    return mounts


def _yaml(value: str) -> str:
    return json.dumps(value)


def _namespace() -> str:
    return "\n".join(
        [
            "# Refined from compose.grpc.yml. Kompose's translation is the",
            "# mechanical step; this file adds the namespace, probes, resources,",
            "# the shared FIFO and result volumes, and a Job for the generator.",
            "apiVersion: v1",
            "kind: Namespace",
            "metadata:",
            "  name: matchmaking",
            "---",
        ]
    )


def _pvc(name: str) -> str:
    return "\n".join(
        [
            "apiVersion: v1",
            "kind: PersistentVolumeClaim",
            "metadata:",
            f"  name: {name}",
            "  namespace: matchmaking",
            "spec:",
            "  accessModes:",
            "    - ReadWriteOnce",
            "  resources:",
            "    requests:",
            "      storage: 1Gi",
            "---",
        ]
    )


def _deployment(service: Service) -> str:
    lines = _pod_header("apps/v1", "Deployment", service)
    lines.extend(
        [
            "spec:",
            "  replicas: 1",
            "  strategy:",
            "    type: Recreate",
            "  selector:",
            "    matchLabels:",
            f"      app.kubernetes.io/component: {service.name}",
        ]
    )
    lines.extend(_pod_template(service, restart="Always"))
    lines.append("---")
    return "\n".join(lines)


def _job(service: Service, plan: Plan) -> str:
    lines = _pod_header("batch/v1", "Job", service)
    lines.extend(
        [
            "spec:",
            "  backoffLimit: 1",
            "  ttlSecondsAfterFinished: 86400",
            "  activeDeadlineSeconds: 900",
        ]
    )
    if plan.count is not None:
        lines.append(f"  # Finite generator run of {plan.count} tickets.")
    lines.extend(_pod_template(service, restart="Never"))
    lines.append("---")
    return "\n".join(lines)


def _pod_header(api: str, kind: str, service: Service) -> list[str]:
    return [
        f"apiVersion: {api}",
        f"kind: {kind}",
        "metadata:",
        f"  name: {service.name}",
        "  namespace: matchmaking",
        "  labels:",
        "    app.kubernetes.io/name: matchmaking",
        f"    app.kubernetes.io/component: {service.name}",
    ]


def _pod_template(service: Service, *, restart: str) -> list[str]:
    lines = [
        "  template:",
        "    metadata:",
        "      labels:",
        "        app.kubernetes.io/name: matchmaking",
        f"        app.kubernetes.io/component: {service.name}",
        "    spec:",
        f"      restartPolicy: {restart}",
        "      containers:",
        f"      - name: {service.name}",
        f"        image: {IMAGE}",
        "        imagePullPolicy: IfNotPresent",
    ]
    if service.command:
        lines.append("        args:")
        for arg in service.command:
            lines.append(f"          - {_yaml(arg)}")
    if service.environment:
        lines.append("        env:")
        for key in sorted(service.environment):
            lines.append(f"        - name: {key}")
            lines.append(f"          value: {_yaml(service.environment[key])}")
    if service.listen_port is not None:
        lines.extend(
            [
                "        ports:",
                "        - name: grpc",
                f"          containerPort: {service.listen_port}",
            ]
        )
    lines.extend(_probes(service))
    lines.extend(
        [
            "        resources:",
            "          requests:",
            '            cpu: "50m"',
            "            memory: 64Mi",
            "          limits:",
            '            cpu: "1"',
            "            memory: 256Mi",
        ]
    )
    mounts = []
    volumes = []
    if service.mount_fifos:
        mounts.append(("fifos", "/fifos"))
        volumes.append(("fifos", "matchmaking-fifos"))
    if service.mount_shared:
        mounts.append(("results", "/shared"))
        volumes.append(("results", "matchmaking-results"))
    if mounts:
        lines.append("        volumeMounts:")
        for name, path in mounts:
            lines.append(f"        - name: {name}")
            lines.append(f"          mountPath: {path}")
    if volumes:
        lines.append("      volumes:")
        for name, claim in volumes:
            lines.append(f"      - name: {name}")
            lines.append("        persistentVolumeClaim:")
            lines.append(f"          claimName: {claim}")
    return lines


def _probes(service: Service) -> list[str]:
    if service.health == "tcp":
        check = ["          tcpSocket:", "            port: grpc"]
    elif service.health == "none":
        return []
    else:
        check = [
            "          exec:",
            "            command:",
            "            - python",
            f"            - {_yaml(HEALTHCHECK)}",
        ]
    lines: list[str] = []
    for name, period, failures in (
        ("startupProbe", 2, 90),
        ("readinessProbe", 2, 3),
        ("livenessProbe", 20, 3),
    ):
        lines.append(f"        {name}:")
        lines.extend(check)
        lines.append(f"          periodSeconds: {period}")
        lines.append(f"          failureThreshold: {failures}")
        lines.append("          timeoutSeconds: 3")
    return lines


def _service(service: Service) -> str:
    port = service.listen_port
    return "\n".join(
        [
            "apiVersion: v1",
            "kind: Service",
            "metadata:",
            f"  name: {service.name}",
            "  namespace: matchmaking",
            "  labels:",
            "    app.kubernetes.io/name: matchmaking",
            "spec:",
            "  selector:",
            f"    app.kubernetes.io/component: {service.name}",
            "  ports:",
            "  - name: grpc",
            f"    port: {port}",
            "    targetPort: grpc",
            "---",
        ]
    )
