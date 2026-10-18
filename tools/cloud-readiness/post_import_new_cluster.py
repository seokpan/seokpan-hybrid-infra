"""Bind the accepted read-only SQL probe to a recreated cluster; no creation or app start."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
CONTRACT_SHA = "61f2b3234634e9b62ee7a6b1ae334d13ed178ea6d7c1a206516c7e0b39326296"
PROBE_SHA = "e9220546fdbf1f8162b17f90e0cc719ad92c501c809f27a9af359e55e5fa1d37"


class Stop(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Stop(code)


def load_contract():
    path = HERE / "accepted_sql_contract.py"
    need(hashlib.sha256(path.read_bytes()).hexdigest() == CONTRACT_SHA, "ACCEPTED_SQL_CONTRACT_REQUIRED")
    spec = importlib.util.spec_from_file_location("accepted_sql_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_private(path, limit=4 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) in (0o400, 0o600), "PRIVATE_OWNED_INPUT_REQUIRED")
        need(info.st_size <= limit, "BOUNDED_INPUT_REQUIRED")
        return stream.read(limit + 1)


def target_identity(lifecycle, platform, target):
    cluster = lifecycle.get("current_cluster_id")
    need(isinstance(cluster, str) and cluster and cluster != lifecycle.get("source_cluster_id"),
         "RECREATED_CLUSTER_ID_REQUIRED_NO_OLD_TARGET")
    need(platform.get("cluster_id") == cluster and platform.get("state") == "ready",
         "CURRENT_READY_PLATFORM_HANDOFF_REQUIRED")
    need(target.get("schema") == 1 and target.get("cluster_id") == cluster
         and target.get("namespace") == "seokpan-cloud"
         and target.get("readonly_pod_scope_accepted") is True, "NEW_SQL_TARGET_BINDING_REQUIRED")
    api = platform.get("api_url", "")
    parsed = urlsplit(api)
    need(parsed.scheme == "https" and parsed.hostname and parsed.port == 6443
         and parsed.hostname.startswith("api.") and parsed.hostname.endswith(".openshiftapps.com")
         and not parsed.username and not parsed.password and parsed.path in ("", "/")
         and not parsed.query and not parsed.fragment, "EXPECTED_NEW_ROSA_HTTPS_API_REQUIRED")
    for name in ("namespace_uid", "pod_uid", "secret_uid", "ca_uid"):
        need(isinstance(target.get(name), str)
             and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", target[name]),
             "NEW_OBJECT_UID_BINDING_REQUIRED")
    need(re.fullmatch(r"data-admin-[a-z0-9-]+", target.get("pod_name", "")), "DATA_OPERATOR_POD_NAME_REQUIRED")
    need(target["pod_uid"] != "97b7b533-b72c-43dd-8afd-0100bcb144aa", "OLD_DATA_POD_UID_REJECTED")
    need(target.get("container") == "db-admin", "DATA_CONTAINER_REQUIRED")
    need(re.fullmatch(r"[0-9a-f]{64}", target.get("held_bundle_sha256", "")), "CURRENT_HELD_BUNDLE_HASH_REQUIRED")
    return cluster, api.rstrip("/")


def execute(lifecycle, platform, target, handoff, held_raw, contract, probe, runner=subprocess.run):
    # Human final-Import/writer evidence is required before any API, credential or SQL access.
    contract.accepted_handoff(handoff)
    cluster, api = target_identity(lifecycle, platform, target)
    need(hashlib.sha256(held_raw).hexdigest() == target["held_bundle_sha256"], "CURRENT_HELD_BUNDLE_HASH_MISMATCH")
    need(hashlib.sha256(probe).hexdigest() == PROBE_SHA, "ACCEPTED_READ_ONLY_PROBE_REQUIRED")
    held = json.loads(held_raw)
    rows = held.get("items", [])
    backend = [o for o in rows if o.get("kind") == "Deployment" and o.get("metadata", {}).get("name") == "backend"]
    need(len(backend) == 1 and backend[0]["metadata"].get("namespace") == "seokpan-cloud"
         and backend[0]["spec"].get("replicas") == 0, "CURRENT_HELD_BACKEND_REQUIRED")
    image = next(c["image"] for c in backend[0]["spec"]["template"]["spec"]["containers"] if c["name"] == "backend")
    need(re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}", image), "APPROVED_DIGEST_IMAGE_REQUIRED")
    prefix = [target["oc"], "--kubeconfig=" + target["personal_kubeconfig"], "--request-timeout=20s"]
    def call(*args, payload=None):
        result = runner(prefix + list(args), input=payload, capture_output=True,
                        timeout=90 if args[0] == "exec" else 35)
        need(result.returncode == 0, "CURRENT_TARGET_READ_OR_EXEC_FAILED_NO_RETRY")
        return result.stdout
    cfg = json.loads(call("config", "view", "--minify", "-o", "json"))
    transport = cfg["clusters"][0]["cluster"]
    need(transport.get("server", "").rstrip("/") == api
         and not transport.get("insecure-skip-tls-verify"), "CURRENT_TARGET_TLS_TRUST_REQUIRED")
    need(call("whoami").strip() == b"tjung03", "CURRENT_PERSONAL_TJUNG03_REQUIRED")
    ns = json.loads(call("get", "namespace", "seokpan-cloud", "-o", "json"))
    need(ns["metadata"]["uid"] == target["namespace_uid"] and not ns["metadata"].get("deletionTimestamp"),
         "CURRENT_NAMESPACE_UID_REQUIRED")
    pod = json.loads(call("get", "pod", target["pod_name"], "-n", "seokpan-cloud", "-o", "json"))
    containers = pod.get("spec", {}).get("containers", [])
    need(pod["metadata"]["uid"] == target["pod_uid"] and not pod["metadata"].get("deletionTimestamp")
         and len(containers) == 1 and containers[0].get("name") == "db-admin"
         and containers[0].get("image") == image and pod.get("status", {}).get("phase") == "Running"
         and any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod["status"].get("conditions", [])),
         "CURRENT_READY_DATA_OPERATOR_POD_REQUIRED")
    mounts = {m["name"]: m for m in containers[0].get("volumeMounts", [])}
    need(any(v.get("configMap", {}).get("name") == "backend-database-ca"
             and mounts.get(v["name"], {}).get("mountPath") == "/run/rds-ca"
             and mounts[v["name"]].get("readOnly") is True for v in pod["spec"].get("volumes", [])),
         "READ_ONLY_DATABASE_CA_MOUNT_REQUIRED")
    secret = json.loads(call("get", "secret", "backend-db-runtime", "-n", "seokpan-cloud", "-o", "json"))
    ca = json.loads(call("get", "configmap", "backend-database-ca", "-n", "seokpan-cloud", "-o", "json"))
    need(secret["metadata"]["uid"] == target["secret_uid"] and ca["metadata"]["uid"] == target["ca_uid"],
         "CURRENT_PROTECTED_OBJECT_UID_REQUIRED")
    payload = contract.runtime_payload(held, secret, ca["data"]["ca.crt"], handoff)
    result = json.loads(call("exec", "-i", "-n", "seokpan-cloud", target["pod_name"], "-c", "db-admin", "--", "python", "-c",
                             probe.decode("utf-8"), payload=json.dumps(payload).encode("utf-8")))
    need(result.get("status") in ("PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION", "STOP_POST_IMPORT_PURPOSE_READS"),
         "EXPECTED_SANITIZED_PROBE_RESULT_REQUIRED")
    result.update(cluster_id=cluster, new_cluster_bound=True, old_token_or_pod_reused=False,
                  handoff_assertions="HUMAN_ACCEPTED_NOT_INDEPENDENT_REPROOF")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-config", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--execute-read-only", action="store_true")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = {"status": "STOP_POST_IMPORT_NEW_CLUSTER", "database_writes": 0,
              "application_started": False, "automatic_retry": False, "credential_values_printed": False}
    try:
        contract = load_contract()
        handoff = json.loads(read_private(args.handoff))
        contract.accepted_handoff(handoff)
        lifecycle = json.loads(read_private(args.lifecycle_config))
        platform = json.loads(read_private(Path(lifecycle["create"]["last_platform_handoff"])))
        target = json.loads(read_private(args.target))
        cluster, _ = target_identity(lifecycle, platform, target)
        if args.execute_read_only:
            read_private(Path(target["personal_kubeconfig"]))
            held_raw = read_private(Path(target["held_bundle"]))
            result = execute(lifecycle, platform, target, handoff, held_raw, contract,
                             (HERE / "accepted_sql_probe.py").read_bytes())
        else:
            result.update(status="LOCAL_NEW_SQL_TARGET_ACCEPTED_NOT_EXECUTED", cluster_id=cluster, api_calls=0)
    except Exception as error:
        result["reason"] = str(error) if type(error) is Stop or type(error).__module__ == "accepted_sql_contract" else type(error).__name__
    fd = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))
    return 0 if result["status"].startswith(("PASS_", "LOCAL_")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
