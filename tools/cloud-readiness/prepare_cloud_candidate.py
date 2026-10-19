"""Compile local activation previews only. Never authenticates, applies or starts apps."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

STAGES = {"single": (1, 1), "shared": (2, 1), "target": (3, 3)}
SOURCE_FILES = (
    "apps/base/kustomization.yaml", "apps/base/backend.yaml", "apps/base/frontend.yaml",
    "apps/base/services.yaml", "apps/base/runtime.env",
    "apps/overlays/cloud/runtime/kustomization.yaml", "apps/overlays/cloud/runtime/routes.yaml",
    "apps/overlays/cloud/runtime/runtime.env", "apps/overlays/cloud/runtime/release-source.yaml",
    "apps/overlays/cloud/activation-target/topology.yaml",
)


def need(ok, code):
    if not ok:
        raise ValueError(code)


def host(value):
    need(isinstance(value, str) and len(value) <= 253
         and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value)
         and all(part and len(part) <= 63 and not part.startswith("-")
                 and not part.endswith("-") for part in value.split(".")), "DNS_HOST_ONLY_REQUIRED")
    return value


def configuration(inputs):
    allowed = {"route_host", "database_host", "redis_host", "ws_timeout"}
    need(set(inputs) <= allowed, "ONLY_NON_SECRET_CONNECTION_INPUTS_ALLOWED")
    route = host(inputs.get("route_host", "rosa-route-host-input-required.invalid"))
    database = host(inputs.get("database_host", "rds-endpoint-input-required.invalid"))
    redis = host(inputs.get("redis_host", "redis-primary-endpoint-input-required.invalid"))
    timeout = inputs.get("ws_timeout", "5m")
    need(isinstance(timeout, str) and re.fullmatch(r"[1-9][0-9]*(?:s|m)", timeout), "WS_TIMEOUT_REQUIRED")
    env = {
        "SEOKPAN_CONNECTION_PROFILE": "cloud", "SEOKPAN_GAME_LIFECYCLE_MODE": "captured",
        "SEOKPAN_ALLOWED_ORIGINS": json.dumps(["https://" + route], separators=(",", ":")),
        "SEOKPAN_DATABASE_EXPECTED_HOST": database, "SEOKPAN_DATABASE_EXPECTED_PORT": "3306",
        "SEOKPAN_DATABASE_EXPECTED_NAME": "stone_game",
        "SEOKPAN_REDIS_URL": "rediss://" + redis + ":6379/0",
        "SEOKPAN_REDIS_EXPECTED_HOST": redis, "SEOKPAN_REDIS_EXPECTED_PORT": "6379",
        "SEOKPAN_REDIS_EXPECTED_DATABASE": "0",
        "SEOKPAN_DATABASE_POOL_SIZE": "2", "SEOKPAN_DATABASE_MAX_OVERFLOW": "0",
        "SEOKPAN_DATABASE_POOL_TIMEOUT_SECONDS": "5",
    }
    pending = [key for key, value in (("route_host", route), ("database_host", database),
                                     ("redis_host", redis)) if value.endswith(".invalid")]
    return env, route, timeout, pending


def stage_overlay(name, inputs):
    need(name in STAGES, "KNOWN_STAGE_REQUIRED")
    backend, frontend = STAGES[name]
    env, route, timeout, pending = configuration(inputs)
    patches = []
    for deployment in ("backend", "frontend"):
        pod = {"serviceAccountName": "seokpan-cloud-runtime"}
        if deployment == "backend":
            pod["containers"] = [{"name": "backend", "command": ["uvicorn"],
                                  "args": ["seokpan.app:app", "--app-dir", "src", "--host",
                                           "0.0.0.0", "--port", "8000", "--workers", "1"]}]
        patches.append({"target": {"group": "apps", "version": "v1", "kind": "Deployment", "name": deployment},
                        "patch": json.dumps({"apiVersion": "apps/v1", "kind": "Deployment",
                                             "metadata": {"name": deployment},
                                             "spec": {"template": {"spec": pod}}})})
    for name in ("frontend", "backend-api", "backend-ws"):
        patch = [{"op": "replace", "path": "/spec/host", "value": route}]
        if name == "backend-ws":
            patch.append({"op": "add", "path": "/metadata/annotations",
                          "value": {"haproxy.router.openshift.io/timeout-tunnel": timeout}})
        patches.append({"target": {"group": "route.openshift.io", "version": "v1", "kind": "Route", "name": name},
                        "patch": json.dumps(patch)})
    resources = ["../runtime"]
    if backend == 3:
        resources.append("pdb.json")
        patches.append({"path": "topology.yaml"})
    overlay = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
               "namespace": "seokpan-cloud", "resources": resources,
               "replicas": [{"name": "backend", "count": backend}, {"name": "frontend", "count": frontend}],
               "configMapGenerator": [{"name": "backend-config", "behavior": "merge", "envs": ["candidate.env"]}],
               "patches": patches}
    return overlay, env, pending


def prepare(source, out, inputs, kustomize):
    # Existing source and output are never edited in place.
    source, out = source.resolve(), out.absolute()
    contract = json.loads(Path(__file__).with_name("source_contract.json").read_text(encoding="utf-8"))
    need(not out.exists(), "NEW_OUTPUT_DIRECTORY_REQUIRED")
    need(source != out and source not in out.parents, "OUTPUT_OUTSIDE_SOURCE_REQUIRED")
    out.mkdir(mode=0o700, parents=True)
    manifest = []
    for name in SOURCE_FILES:
        path = source / name
        need(path.is_file() and not path.is_symlink(), "REGULAR_SOURCE_FILE_REQUIRED")
        data = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        need(blob == contract["files"][name], "PINNED_GITOPS_SOURCE_CONTENT_REQUIRED")
        target = out / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    stages = []
    for name, replicas in STAGES.items():
        overlay, env, pending = stage_overlay(name, inputs)
        directory = out / "source/apps/overlays/cloud" / ("prepared-" + name)
        directory.mkdir()
        (directory / "kustomization.yaml").write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
        (directory / "candidate.env").write_text("".join(k + "=" + v + "\n" for k, v in env.items()), encoding="utf-8")
        if name == "target":
            (directory / "topology.yaml").write_bytes(
                (out / "source/apps/overlays/cloud/activation-target/topology.yaml").read_bytes())
            pdbs = {"apiVersion": "v1", "kind": "List", "items": [
                {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": app},
                 "spec": {"minAvailable": 2, "selector": {"matchLabels": {"app.kubernetes.io/name": app}}}}
                for app in ("backend", "frontend")]}
            (directory / "pdb.json").write_text(json.dumps(pdbs, indent=2) + "\n", encoding="utf-8")
        result = subprocess.run([str(kustomize), "build", str(directory)], capture_output=True, timeout=40)
        need(result.returncode == 0, "LOCAL_KUSTOMIZE_BUILD_FAILED")
        rendered = out / (name + ".DO_NOT_APPLY.yaml")
        rendered.write_bytes(result.stdout)
        stages.append({"stage": name, "backend": replicas[0], "frontend": replicas[1],
                       "unresolved_hosts": pending, "render_sha256": hashlib.sha256(result.stdout).hexdigest()})
    receipt = {"status": "PREPARED_THREE_LOCAL_ACTIVATION_CANDIDATES_NOT_APPLIED", "stages": stages,
               "pool_size_candidate": 2, "max_overflow_candidate": 0, "pool_timeout_seconds_candidate": 5,
               "ws_timeout_candidate": inputs.get("ws_timeout", "5m"), "processes_per_backend": 1,
               "peak_backend_pods_candidate": 7, "peak_app_connections_candidate": 28,
               "reserved_connections": 10, "database_limit_accepted_previously": 68,
               "remaining_candidate_connections": 30,
               "runtime_values_adopted": False, "lifecycle_data_transition_accepted": False,
               "final_import_accepted": False, "server_render_accepted": False, "app_started": False,
               "api_calls": 0, "sql_calls": 0, "resource_mutations": 0,
               "source_files": manifest, "source_release_unchanged": True}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--kustomize", type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = json.loads(args.inputs.read_text(encoding="utf-8")) if args.inputs else {}
        receipt = prepare(args.source, args.out, inputs, args.kustomize)
        print(json.dumps({key: value for key, value in receipt.items() if key != "source_files"}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "STOP_LOCAL_ACTIVATION_PREPARATION", "reason": str(error)
                          if type(error) is ValueError else type(error).__name__, "api_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
