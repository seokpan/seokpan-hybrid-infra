"""Read-only source stop evidence after the reviewed Git maintenance PR merges."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request

REPO = "seokpan/seokpan-gitops"
DEPLOYMENT_PATH = "apps/backend/deployment.yaml"


class Stop(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise Stop(reason)


def fetch(url, json_result=True):
    request = urllib.request.Request(url, headers={"User-Agent": "seokpan-source-stop-gate", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    require(len(data) <= 2 * 1024 * 1024, "GITHUB_RESPONSE_TOO_LARGE")
    return json.loads(data) if json_result else data


def merged_source(pr_number, expected_hash):
    api = "https://api.github.com/repos/" + REPO + "/pulls/" + str(pr_number)
    pr = fetch(api)
    if pr.get("merged") is not True:
        return None
    require(pr.get("base", {}).get("ref") == "main" and
            pr.get("base", {}).get("repo", {}).get("full_name") == REPO, "PR_BASE_CHANGED")
    sha = pr.get("merge_commit_sha", "")
    require(isinstance(sha, str) and re.fullmatch("[0-9a-f]{40}", sha), "MERGE_SHA_REQUIRED")
    files = fetch(api + "/files?per_page=100")
    require(len(files) == 1 and files[0].get("filename") == DEPLOYMENT_PATH and
            files[0].get("status") == "modified", "PR_SINGLE_DEPLOYMENT_SCOPE_CHANGED")
    content = fetch("https://raw.githubusercontent.com/" + REPO + "/" + sha + "/" + DEPLOYMENT_PATH, False)
    require(hashlib.sha256(content).hexdigest() == expected_hash, "MERGED_MANIFEST_HASH_CHANGED")
    require(content.count(b"\n  replicas: 0\n") == 1, "MERGED_ZERO_REPLICAS_REQUIRED")
    return sha


def expected_inputs(contract, source, api):
    require(contract.get("status") == "SOURCE_GIT_CONTROL_CONTRACT_COLLECTED", "ACCEPTED_GIT_CONTRACT_REQUIRED")
    require(source.get("status") == "SOURCE_LOCAL_READ_COMPLETED", "ACCEPTED_SOURCE_READ_REQUIRED")
    applications = {item["name"]: item for item in contract["applications"]}
    require({"root", "apps-backend"} <= applications.keys(), "ROOT_AND_CHILD_CONTRACT_REQUIRED")
    rows = [item for item in source["contexts"] if item.get("source_contract_match") is True]
    require(rows and len({item.get("deployment_uid") for item in rows}) == 1 and
            all(item.get("deployment_uid") and item.get("server_sha256") == hashlib.sha256(api.rstrip("/").encode()).hexdigest() for item in rows),
            "SOURCE_IDENTITY_NOT_UNIQUE")
    return {"applications": applications, "deployment_uid": rows[0]["deployment_uid"],
            "kubeconfig": rows[0]["kubeconfig"], "context": rows[0]["context"]}


class Client:
    def __init__(self, expected):
        self.prefix = ["kubectl", "--kubeconfig=" + expected["kubeconfig"],
                       "--context=" + expected["context"], "--request-timeout=20s"]

    def read(self, *args):
        result = subprocess.run(self.prefix + list(args), capture_output=True, timeout=30)
        require(result.returncode == 0, "SOURCE_READ_FAILED_NO_RAW_OUTPUT")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def snapshot(self):
        return {
            "applications": self.read("get", "applications.argoproj.io", "root", "apps-backend", "-n", "argocd", "-o", "json")["items"],
            "deployment": self.read("get", "deployment", "backend", "-n", "application", "-o", "json"),
            "service": self.read("get", "service", "backend", "-n", "application", "-o", "json"),
            "pods": self.read("get", "pods", "-n", "application", "-l", "app.kubernetes.io/name=backend", "-o", "json")["items"],
            "slices": self.read("get", "endpointslices.discovery.k8s.io", "-n", "application", "-l", "kubernetes.io/service-name=backend", "-o", "json")["items"],
            "endpoints": self.read("get", "endpoints", "backend", "-n", "application", "--ignore-not-found=true", "-o", "json"),
            "controllers": self.read("get", "hpa,jobs,cronjobs", "-n", "application", "-o", "json")["items"]}


def evaluate(snapshot, expected, merge_sha):
    for app in snapshot["applications"]:
        name = app["metadata"]["name"]
        baseline = expected["applications"][name]
        require(app["metadata"]["uid"] == baseline["uid"], "APPLICATION_UID_CHANGED")
        spec = app["spec"]
        source = spec.get("source", {})
        require(not spec.get("sources") and source.get("repoURL", "").rstrip("/").removesuffix(".git") == "https://github.com/" + REPO and
                source.get("targetRevision") == "main" and source.get("path") == ("apps/backend" if name == "apps-backend" else "argocd/applications"), "LIVE_SOURCE_CHANGED")
        kustomize = source.get("kustomize") or {}
        require(not kustomize.get("replicas") and not any(key.startswith("patches") for key in kustomize) and
                not source.get("helm") and not source.get("plugin") and not source.get("chart"), "LIVE_SOURCE_OVERRIDE_REVIEW_REQUIRED")
    apps = {app["metadata"]["name"]: app for app in snapshot["applications"]}
    require(set(apps) == {"root", "apps-backend"}, "ROOT_AND_CHILD_REQUIRED")
    dep = snapshot["deployment"]
    require(dep["metadata"]["uid"] == expected["deployment_uid"], "BACKEND_UID_CHANGED")
    require(dep["spec"]["selector"]["matchLabels"] == {"app.kubernetes.io/name": "backend"} and
            snapshot["service"]["spec"].get("selector") == {"app.kubernetes.io/name": "backend"}, "BACKEND_SELECTOR_CHANGED")
    status = dep.get("status", {})
    active_jobs = sum(item.get("status", {}).get("active", 0) or 0 for item in snapshot["controllers"] if item["kind"] == "Job")
    enabled_crons = sum(item["kind"] == "CronJob" and item.get("spec", {}).get("suspend") is not True for item in snapshot["controllers"])
    backend_hpa = sum(item["kind"] == "HorizontalPodAutoscaler" and item.get("spec", {}).get("scaleTargetRef", {}).get("name") == "backend" for item in snapshot["controllers"])
    counts = {"backend_pods_including_terminating": len(snapshot["pods"]),
              "endpoint_addresses_including_not_ready": sum(len(endpoint.get("addresses", [])) for item in snapshot["slices"] for endpoint in item.get("endpoints", [])),
              "legacy_endpoint_addresses_including_not_ready": sum(len(subset.get("addresses", [])) + len(subset.get("notReadyAddresses", [])) for subset in (snapshot["endpoints"] or {}).get("subsets", [])),
              "active_jobs": active_jobs, "enabled_cronjobs": enabled_crons, "backend_hpa": backend_hpa}
    ready = (dep["spec"].get("replicas") == 0 and status.get("observedGeneration", -1) >= dep["metadata"]["generation"] and
             all((status.get(key) or 0) == 0 for key in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas")) and
             not any(counts.values()) and not any(app.get("operation") or app.get("status", {}).get("operationState", {}).get("phase") in ("Running", "Terminating") for app in apps.values()) and
             apps["apps-backend"].get("status", {}).get("sync", {}).get("revision") == merge_sha and
             apps["apps-backend"].get("status", {}).get("sync", {}).get("status") == "Synced")
    return ready, counts


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-contract", type=Path, required=True)
    parser.add_argument("--source-read", type=Path, required=True)
    parser.add_argument("--source-api", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--settle-seconds", type=int, default=30)
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix="seokpan-source-stop-"))
    phase = "ACCEPTED_INPUTS"
    result = None
    try:
        require(re.fullmatch("[0-9a-f]{64}", args.manifest_sha256), "EXPECTED_MANIFEST_HASH_REQUIRED")
        require(1 <= args.pr and 1 <= args.settle_seconds <= 120 and args.settle_seconds <= args.timeout <= 900, "BOUNDED_GATE_ARGUMENTS_REQUIRED")
        expected = expected_inputs(json.loads(args.source_contract.read_text()), json.loads(args.source_read.read_text()), args.source_api)
        phase = "MERGED_GIT_SOURCE"
        merge_sha = merged_source(args.pr, args.manifest_sha256)
        if merge_sha is None:
            result = {"status": "HOLD_SOURCE_STOP_PR_NOT_MERGED", "source_cluster_queries": 0, "resource_mutations": 0, "sql_calls": 0}
            return 0
        phase = "SOURCE_STOP_OBSERVATION"
        client = Client(expected)
        config = client.read("config", "view", "--minify", "-o", "json")
        require(config["clusters"][0]["cluster"]["server"].rstrip("/") == args.source_api.rstrip("/"), "WRONG_SOURCE_CLUSTER")
        deadline, stable_since = time.monotonic() + args.timeout, None
        while True:
            ready, counts = evaluate(client.snapshot(), expected, merge_sha)
            now = time.monotonic()
            stable_since = (stable_since if stable_since is not None else now) if ready else None
            if stable_since is not None and now - stable_since >= args.settle_seconds:
                result = {"status": "PASS_KUBERNETES_BACKEND_STOP_GATE", "merge_sha": merge_sha, "stable_seconds": args.settle_seconds,
                          "counts": counts, "resource_mutations": 0, "sql_calls": 0,
                          "final_dump_requires_external_writer_hold_and_A_replication_checks": True}
                return 0
            require(now < deadline, "STOP_STATE_NOT_CONFIRMED_WITHIN_WINDOW_NO_MUTATIONS")
            time.sleep(5)
    except Exception as error:
        result = {"status": "STOP_SOURCE_BACKEND_GATE", "phase": phase,
                  "reason": str(error) if isinstance(error, Stop) else type(error).__name__, "resource_mutations": 0, "sql_calls": 0}
        return 1
    finally:
        (folder / "receipt.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        print("Private result directory: " + str(folder))


if __name__ == "__main__":
    raise SystemExit(main())
