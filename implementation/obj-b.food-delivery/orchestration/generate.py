#!/usr/bin/env python3
"""Convert the baseline configuration into Compose scripts and a Kompose-based Kubernetes Job."""

import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
APP = HERE.parent
REPO = APP.parents[1]
sys.path.insert(0, str(APP))
from run_demo import validate_config


def write_json(path, data):
    # JSON is also valid YAML, avoiding a separate templating dependency
    path.write_text(json.dumps(data, indent=2) + "\n")


def compose(config, transport, image):
    workers = [f"kitchen-{i}" for i in range(config["kitchen_workers"])] + [
        f"courier-{i}" for i in range(config["couriers"])
    ]
    services = {}
    for name in ["init", "q0", "q1", *workers, "generator"]:
        role = "queue" if name in ("q0", "q1") else "server" if name in workers else name
        service = {
            "image": image,
            "command": [role] + (["--name", name] if role in ("queue", "server") else []),
            "environment": {"FOOD_CONFIG": json.dumps(config), "FOOD_TRANSPORT": transport},
            "volumes": [f"../runs/{transport}:/data"],
            "restart": "no",
        }
        if transport == "fifo":
            service["volumes"].append("ipc:/ipc")
        if name != "init":
            service["depends_on"] = {"init": {"condition": "service_completed_successfully"}}
        if role in ("queue", "server"):
            service["healthcheck"] = {
                "test": ["CMD", "test", "-f", f"/data/run/{name}.ready"],
                "interval": "1s",
                "timeout": "1s",
                "retries": 30,
            }
            if transport == "grpc":
                service["expose"] = [str(50050 + ["q0", "q1", *workers].index(name))]
        if name == "generator":
            service["depends_on"].update(
                {key: {"condition": "service_healthy"} for key in ["q0", "q1", *workers]}
            )
        services[name] = service
    result = {"name": f"food-delivery-{transport}", "services": services}
    if transport == "fifo":
        result["volumes"] = {"ipc": {}}
    return result


def refine(raw, config):
    """Refine actual Kompose containers into one finite Job with shared persisted results."""
    containers = {}
    for item in raw["items"]:
        if item["kind"] == "Pod":
            spec = item["spec"]
        elif item["kind"] in ("Deployment", "Job"):
            spec = item["spec"]["template"]["spec"]
        else:
            continue
        for original in spec["containers"]:
            container = copy.deepcopy(original)
            name = container["name"]
            container["imagePullPolicy"] = "IfNotPresent"
            container["env"] = [
                {"name": "FOOD_TRANSPORT", "value": "grpc"},
                {"name": "FOOD_LOCALHOST", "value": "1"},
                {
                    "name": "FOOD_CONFIG",
                    "valueFrom": {"configMapKeyRef": {"name": "food-config", "key": "config.json"}},
                },
            ]
            container["volumeMounts"] = [{"name": "results", "mountPath": "/data"}]
            container["resources"] = {
                "requests": {"cpu": "100m", "memory": "64Mi"},
                "limits": {"cpu": "1", "memory": "256Mi"},
            }
            # restarting a worker mid-run would lose its in-memory assignment state
            container.pop("livenessProbe", None)
            if name not in ("init", "generator"):
                container["readinessProbe"] = {
                    "exec": {"command": ["test", "-f", f"/data/run/{name}.ready"]},
                    "periodSeconds": 1,
                }
            containers[name] = container
    init = containers.pop("init")
    namespace = "food-delivery"
    metadata = lambda name: {"name": name, "namespace": namespace}
    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": metadata("food-config"),
                "data": {"config.json": json.dumps(config, indent=2)},
            },
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": metadata("food-results"),
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "1Gi"}},
                },
            },
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": metadata("food-delivery"),
                "spec": {
                    "backoffLimit": 0,
                    "activeDeadlineSeconds": 180,
                    "template": {
                        "metadata": {"labels": {"app": "food-delivery"}},
                        "spec": {
                            "restartPolicy": "Never",
                            "automountServiceAccountToken": False,
                            "initContainers": [init],
                            "containers": list(containers.values()),
                            "volumes": [
                                {
                                    "name": "results",
                                    "persistentVolumeClaim": {"claimName": "food-results"},
                                }
                            ],
                        },
                    },
                },
            },
        ],
    }


def generate(config, output, image="food-delivery:local", kompose=None):
    validate_config(config)
    output.mkdir(parents=True, exist_ok=True)
    for transport in ("fifo", "grpc"):
        write_json(output / f"compose.{transport}.yaml", compose(config, transport, image))
    context = shlex.quote(os.path.relpath(REPO, output))
    dockerfile = shlex.quote(os.path.relpath(HERE / "Dockerfile", output))
    script = output / "run.sh"
    script.write_text(f"""#!/bin/sh
set -eu
cd "$(dirname "$0")"
transport=${{1:-grpc}}
case "$transport" in fifo|grpc) ;; *) echo "usage: $0 [fifo|grpc]" >&2; exit 2 ;; esac
compose="compose.$transport.yaml"
mkdir -p "../runs/$transport"
if [ -d "../runs/$transport/run" ]; then
    echo "move ../runs/$transport/run aside before starting a new run" >&2
    exit 1
fi
docker build -t {shlex.quote(image)} -f {dockerfile} {context}
cleanup() {{
    docker compose -f "$compose" logs --no-color > "../runs/$transport/compose.log" 2>&1 || true
    docker compose -f "$compose" down --volumes
}}
trap cleanup EXIT
# start the queues and workers before injecting the finite workload
docker compose -f "$compose" up -d --wait --wait-timeout 60 q0 q1 {' '.join(name for name in compose(config, 'grpc', image)['services'] if name.startswith(('kitchen-', 'courier-')))}
docker compose -f "$compose" run --rm --no-deps generator
""")
    script.chmod(0o755)
    if kompose:
        raw = output / "kompose.json"
        with tempfile.TemporaryDirectory(prefix="food-kompose-") as temporary:
            # convert a clean copy so existing run logs never become ConfigMap inputs
            source = Path(temporary) / "source"
            converted = Path(temporary) / "converted"
            source.mkdir()
            converted.mkdir()
            shutil.copyfile(output / "compose.grpc.yaml", source / "compose.grpc.yaml")
            subprocess.run(
                [
                    kompose,
                    "-f",
                    str(source / "compose.grpc.yaml"),
                    "convert",
                    "--json",
                    "--with-kompose-annotation=false",
                    "--out",
                    str(converted),
                ],
                check=True,
            )
            write_json(
                raw,
                {
                    "apiVersion": "v1",
                    "kind": "List",
                    "items": [
                        json.loads(path.read_text())
                        for path in sorted(converted.glob("*.json"))
                    ],
                },
            )
        write_json(output / "kubernetes.json", refine(json.loads(raw.read_text()), config))
    print(f"Generated orchestration files in {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=APP / "topology/baseline.json")
    parser.add_argument("--out", type=Path, default=HERE / "generated")
    parser.add_argument("--image", default="food-delivery:local")
    parser.add_argument(
        "--kompose",
        nargs="?",
        const="kompose",
        help="convert and refine Kubernetes manifests using this Kompose executable",
    )
    args = parser.parse_args()
    generate(json.loads(args.config.read_text()), args.out.resolve(), args.image, args.kompose)


if __name__ == "__main__":
    main()
