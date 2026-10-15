"""Read existing preparation and discover the original cluster; no mutations."""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

os.umask(0o077)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("input_root")
parser.add_argument("resume_directory")
parser.add_argument("held_directory")
parser.add_argument("--cloud-api", required=True)
args = parser.parse_args()
root = Path(args.input_root).resolve()
previous = Path(args.resume_directory).resolve()
held_directory = Path(args.held_directory).resolve()
if previous.parent != root or held_directory.parent != root:
    raise SystemExit("STOP: PREPARATION_DIRECTORY_OUTSIDE_INPUT_ROOT")
folder = Path(tempfile.mkdtemp(prefix="cloud-cutover-readiness-", dir=root))
read = lambda p: json.loads(Path(p).read_text())
write = lambda name, value: (folder / name).write_text(json.dumps(value, indent=2), encoding="utf-8")
pool = read(previous / "pool-receipt.json")
sg = read(previous / "sg-receipt.json")
held = read(held_directory / "held-render.json")
resources = [{"deployment": o["metadata"]["name"], "containers": [
    {"name": c["name"], "resources": c.get("resources", {})}
    for c in o["spec"]["template"]["spec"]["containers"]]}
    for o in held["items"] if o.get("kind") == "Deployment"]
write("deployment-resources.json", resources)
tool = shutil.which("kubectl") or shutil.which("oc")
if not tool:
    candidate = Path.home() / ".local/share/seokpan/oc-cli-4.20/oc"
    if candidate.is_file():
        tool = str(candidate)

def command(args):
    if not tool:
        return None
    try:
        result = subprocess.run([tool, *args], capture_output=True, timeout=30)
        return json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None

configurations = set(filter(None, os.environ.get("KUBECONFIG", "").split(os.pathsep)))
configurations.add(str(Path.home() / ".kube/config"))
contexts = []
local_config_failures = 0
for filename in sorted(configurations):
    path = Path(filename).expanduser()
    if not path.is_file():
        continue
    config = command(["--kubeconfig", str(path), "config", "view", "-o", "json"])
    if config is None:
        local_config_failures += 1
        continue
    clusters = {c["name"]: c["cluster"].get("server", "").rstrip("/") for c in config.get("clusters", [])}
    for entry in config.get("contexts", []):
        server = clusters.get(entry["context"].get("cluster"), "")
        if server == args.cloud_api.rstrip("/"):
            continue
        if not server.startswith("https://"):
            continue
        contexts.append({"kubeconfig": str(path), "context": entry["name"],
                         "server_sha256": hashlib.sha256(server.encode()).hexdigest()})

def investigate(context):
    prefix = ["--kubeconfig", context["kubeconfig"], "--context", context["context"], "--request-timeout=15s"]
    deployment = command(prefix + ["get", "deployment", "backend", "-n", "application", "-o", "json"])
    application = command(prefix + ["get", "application.argoproj.io", "apps-backend", "-n", "argocd", "-o", "json"])
    row = dict(context, source_contract_match=False)
    if deployment is None or application is None:
        row["read_status"] = "TARGET_NOT_FOUND_OR_ACCESS_UNAVAILABLE"
        return row
    spec = application.get("spec", {})
    source = spec.get("source", {})
    matched = (source.get("repoURL", "").removesuffix(".git").rstrip("/") == "https://github.com/seokpan/seokpan-gitops"
               and source.get("path") == "apps/backend"
               and deployment.get("metadata", {}).get("namespace") == "application"
               and not spec.get("sources"))
    row.update(source_contract_match=matched, read_status="READ_COMPLETED")
    if not matched:
        return row
    parent = application.get("metadata", {})
    template = deployment["spec"]["template"]["spec"]
    row.update(deployment_uid=deployment["metadata"]["uid"], application_uid=parent["uid"],
               replicas=deployment["spec"].get("replicas"), ready_replicas=deployment.get("status", {}).get("readyReplicas", 0),
               termination_grace_seconds=template.get("terminationGracePeriodSeconds"),
               containers=[{"name": c["name"], "image": c.get("image"), "resources": c.get("resources", {})} for c in template["containers"]],
               source_revision=source.get("targetRevision"), sync_status=application.get("status", {}).get("sync", {}).get("status"),
               active_operation=bool(application.get("operation")), sync_policy=spec.get("syncPolicy", {}),
               parent_references=[{"kind": p["kind"], "name": p["name"]} for p in parent.get("ownerReferences", [])],
               parent_tracking={k: v for k, v in parent.get("labels", {}).items() if k == "argocd.argoproj.io/instance"})
    writers = command(prefix + ["get", "hpa,jobs,cronjobs", "-n", "application", "-o", "json"])
    row["writer_inventory_available"] = writers is not None
    row["writer_inventory"] = None if writers is None else [
        {"kind": o["kind"], "name": o["metadata"]["name"], "suspend": o.get("spec", {}).get("suspend"),
         "scale_target": o.get("spec", {}).get("scaleTargetRef"), "active": o.get("status", {}).get("active")}
        for o in writers.get("items", [])]
    return row

print(json.dumps({"status": "STARTING_ORIGINAL_CONTEXT_READ_ONLY_DISCOVERY", "non_rosa_contexts": len(contexts)}), flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    rows = list(executor.map(investigate, contexts))
write("original-cluster-readiness.json", rows)
matches = {(r["server_sha256"], r["deployment_uid"], r["application_uid"]) for r in rows if r["source_contract_match"]}
leases = []
for path in root.glob("cloud-connection-*/ecr-pull-receipt.json"):
    try:
        expiry = read(path)["expires_at_utc"]
        datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        leases.append(expiry)
    except (OSError, KeyError, ValueError, TypeError):
        pass
expiry = max(leases, key=lambda v: datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))) if leases else None
expired = None if expiry is None else datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= datetime.datetime.now(datetime.timezone.utc)
summary = {"status": "READ_ONLY_CUTOVER_PREPARATION_COLLECTED", "existing_pool_and_sg_results_reused": True,
           "pool_total_budget": pool["total_budget"], "pool_runtime_adopted": pool["runtime_adopted"],
           "foundation_plan_direct_comparison_pending": sg["foundation_plan_read_access_pending"],
           "cloud_resource_configuration": resources, "local_config_read_failures": local_config_failures,
           "original_cluster_contract_match_count": len(matches), "contexts": rows,
           "temporary_pull_receipt_expiry_utc": expiry, "temporary_pull_receipt_expired": expired,
           "pull_secret_current_contents_or_native_worker_pull": "NOT_QUERIED",
           "all_writers_or_parent_controls_accepted": False, "writer_stop_ready": False,
           "sql_calls": 0, "cluster_mutations": 0, "writer_stop": False, "application_started": False}
write("receipt.json", summary)
print(json.dumps(summary))
print("Private result directory: " + str(folder))
