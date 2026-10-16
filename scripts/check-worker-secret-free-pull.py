"""Test approved images on existing compute nodes without matching Pull Secrets."""
import argparse
import base64
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

IMAGES = {
    "backend": "847835841591.dkr.ecr.ap-northeast-2.amazonaws.com/seokpan-fnd-backend@sha256:733f52a6f5a53c31e792cbe1d254221ff8ff86828e8c3afdc65d207e2e5fb18d",
    "frontend": "847835841591.dkr.ecr.ap-northeast-2.amazonaws.com/seokpan-fnd-frontend@sha256:7068a36f55427639b7b7bf8a9c5795689b891e7ec334bfd306d8cd03c5f049ff"}
REGISTRY = IMAGES["backend"].split("/")[0]
NS = "seokpan-cloud"
RUN_LABEL = "seokpan.io/native-pull-run"


class Stop(Exception):
    pass


def require(value, code):
    if not value:
        raise Stop(code)


def has_registry_credentials(secret):
    data = secret.get("data", {})
    key = ".dockerconfigjson" if ".dockerconfigjson" in data else ".dockercfg"
    require(key in data, "PULL_SECRET_FORMAT_UNKNOWN")
    document = json.loads(base64.b64decode(data[key], validate=True))
    if document.get("credsStore") or document.get("credHelpers"):
        return True
    entries = document.get("auths", document)
    return any(fnmatch.fnmatchcase(REGISTRY, name.removeprefix("https://").removeprefix("http://").split("/")[0]) for name in entries)


def compute_nodes(document):
    nodes = [n for n in document["items"] if "node-role.kubernetes.io/worker" in n["metadata"].get("labels", {})
             and not any("node-role.kubernetes.io/" + role in n["metadata"].get("labels", {}) for role in ("infra", "master", "control-plane"))]
    require(len(nodes) == 3, "THREE_EXISTING_COMPUTE_NODES_REQUIRED")
    return nodes


def probe(name, node, repository, service_account, run):
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": NS, "labels": {RUN_LABEL: run}},
            "spec": {"restartPolicy": "Never", "activeDeadlineSeconds": 180, "terminationGracePeriodSeconds": 1,
                     "serviceAccountName": service_account, "automountServiceAccountToken": False, "imagePullSecrets": [],
                     "nodeSelector": {"kubernetes.io/hostname": node["metadata"]["labels"]["kubernetes.io/hostname"]},
                     "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                     "containers": [{"name": "probe", "image": IMAGES[repository], "imagePullPolicy": "Always",
                                     "command": ["/bin/sh", "-c", "exit 0"],
                                     "resources": {"requests": {"cpu": "25m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}},
                                     "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}}]}}


class Client:
    def __init__(self, oc, kubeconfig):
        self.oc = oc
        self.env = dict(os.environ, KUBECONFIG=str(kubeconfig))

    def run(self, args, payload=None):
        result = subprocess.run([self.oc, "--request-timeout=25s", *args], input=payload, env=self.env, capture_output=True, timeout=40)
        if result.returncode:
            text = result.stderr.decode(errors="replace").lower()
            raise Stop("ACCESS_DENIED" if "forbidden" in text or "unauthorized" in text else "API_OPERATION_FAILED")
        return result.stdout

    def get(self, args):
        raw = self.run(["get", *args, "-o", "json"])
        return json.loads(raw) if raw.strip() else None

    def create(self, obj):
        return json.loads(self.run(["create", "-f", "-", "-o", "json"], json.dumps(obj).encode()))


def cleanup_one(client, kind, name, uid, run):
    current = client.get([kind, name, "-n", NS, "--ignore-not-found"])
    if current is None:
        return
    require(current["metadata"]["uid"] == uid and current["metadata"].get("labels", {}).get(RUN_LABEL) == run, "CLEANUP_IDENTITY_CHANGED_PRESERVED")
    plural = "pods" if kind == "pod" else "serviceaccounts"
    options = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid}, "gracePeriodSeconds": 0}
    client.run(["delete", "--raw=/api/v1/namespaces/" + NS + "/" + plural + "/" + name, "-f", "-"], json.dumps(options).encode())


def check_admitted_pod(client, pod, account, checked_secrets):
    require(pod["spec"].get("serviceAccountName") == account and pod["spec"].get("automountServiceAccountToken") is False, "ADMITTED_POD_IDENTITY_CHANGED")
    require(pod["spec"]["containers"][0]["imagePullPolicy"] == "Always", "PULL_POLICY_CHANGED")
    for reference in pod["spec"].get("imagePullSecrets", []):
        name = reference["name"]
        if name not in checked_secrets:
            secret = client.get(["secret", name, "-n", NS])
            require(not has_registry_credentials(secret), "ADMITTED_ECR_PULL_SECRET_WOULD_MASK_NATIVE_PATH")
            checked_secrets.add(name)


def execute(client, folder):
    run = uuid.uuid4().hex[:12]
    account = "native-pull-" + run
    owned, attempted, results = [], [], []
    failure, cleanup_errors = None, []
    try:
        global_secret = client.get(["secret", "pull-secret", "-n", "openshift-config"])
        require(not has_registry_credentials(global_secret), "GLOBAL_ECR_PULL_SECRET_WOULD_MASK_NATIVE_PATH")
        global_secret.clear()
        nodes = compute_nodes(client.get(["nodes"]))
        sa = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": account, "namespace": NS, "labels": {RUN_LABEL: run}}, "automountServiceAccountToken": False, "imagePullSecrets": []}
        attempted.append(("serviceaccount", account))
        created = client.create(sa)
        owned.append(("serviceaccount", account, created["metadata"]["uid"]))
        expected = {}
        for number, node in enumerate(nodes, 1):
            for repository in IMAGES:
                name = "native-pull-" + run + "-" + str(number) + "-" + repository
                attempted.append(("pod", name))
                pod = client.create(probe(name, node, repository, account, run))
                owned.append(("pod", name, pod["metadata"]["uid"]))
                digest = IMAGES[repository].split("@", 1)[1]
                listed = any(digest in image for row in node.get("status", {}).get("images", []) for image in row.get("names", []))
                expected[name] = {"uid": pod["metadata"]["uid"], "repository": repository, "node": node["metadata"]["name"], "approved_digest_listed_before": listed}
        checked_secrets = set()
        deadline = time.monotonic() + 180
        while True:
            pods = client.get(["pods", "-n", NS, "-l", RUN_LABEL + "=" + run])["items"]
            results = []
            for pod in pods:
                name = pod["metadata"]["name"]
                require(name in expected and pod["metadata"]["uid"] == expected[name]["uid"], "PROBE_IDENTITY_CHANGED")
                check_admitted_pod(client, pod, account, checked_secrets)
                status = pod.get("status", {})
                waiting = [s.get("state", {}).get("waiting", {}).get("reason") for s in status.get("containerStatuses", [])]
                require(not any(x in ("ErrImagePull", "ImagePullBackOff", "InvalidImageName", "CreateContainerConfigError") for x in waiting), "PROBE_PULL_OR_CONFIGURATION_FAILED")
                require(status.get("phase") != "Failed", "PROBE_EXECUTION_FAILED")
                completed = status.get("phase") == "Succeeded"
                if completed:
                    require(status.get("nodeName", pod["spec"].get("nodeName")) == expected[name]["node"], "PROBE_RAN_ON_OTHER_NODE")
                    containers = status.get("containerStatuses", [])
                    require(len(containers) == 1 and containers[0].get("state", {}).get("terminated", {}).get("exitCode") == 0 and containers[0].get("imageID"), "PROBE_EXIT_OR_IMAGE_ID_MISSING")
                results.append({"repository": expected[name]["repository"], "compute_node_number": sorted(n["metadata"]["name"] for n in nodes).index(expected[name]["node"]) + 1,
                                "succeeded": completed, "approved_digest_listed_before": expected[name]["approved_digest_listed_before"]})
            if len(results) == 6 and all(r["succeeded"] for r in results):
                break
            require(time.monotonic() < deadline, "PROBE_DEADLINE_NO_AUTOMATIC_RETRY")
            time.sleep(5)
    except Exception as error:
        failure = str(error) if isinstance(error, Stop) else type(error).__name__
    finally:
        for kind, name, uid in reversed(owned):
            try:
                cleanup_one(client, kind, name, uid, run)
            except Exception as error:
                cleanup_errors.append({"kind": kind, "name": name, "reason": str(error) if isinstance(error, Stop) else type(error).__name__})
    unknown = [(kind, name) for kind, name in attempted if not any(kind == k and name == n for k, n, uid in owned)]
    if owned and not cleanup_errors and not unknown:
        try:
            deadline = time.monotonic() + 30
            while True:
                remaining = sum(len(client.get([kind, "-n", NS, "-l", RUN_LABEL + "=" + run])["items"]) for kind in ("pods", "serviceaccounts"))
                if remaining == 0:
                    break
                require(time.monotonic() < deadline, "OWNED_PROBE_REMOVAL_NOT_CONFIRMED")
                time.sleep(2)
        except Exception as error:
            cleanup_errors.append({"kind": "owned-probes", "reason": str(error) if isinstance(error, Stop) else type(error).__name__})
    summary = {"status": "PASS_SECRET_FREE_WORKER_PULL_AND_EXECUTION" if failure is None and not cleanup_errors and not unknown else "STOP_SECRET_FREE_WORKER_PULL",
               "reason": failure, "results": results, "temporary_pods_created": sum(k == "pod" for k, n, uid in owned),
               "cleanup": "PASS_OWN_PODS_AND_SERVICEACCOUNT_REMOVED" if not cleanup_errors and not unknown else "REQUIRES_ATTENTION",
               "cleanup_errors": cleanup_errors, "create_outcome_unknown": unknown,
               "namespace_or_global_matching_credentials": "NONE_ACCEPTED" if failure is None else "CHECK_RESULT",
               "worker_role_credential_causal_trace": "NOT_INDEPENDENTLY_OBSERVED_CACHE_AND_PROVIDER_BEHAVIOR_NOT_ASSERTED",
               "new_node_replacement_and_12_hour_expiry": "NOT_TESTED", "app_server_started": False, "sql_calls": 0, "database_writes": 0,
               "iam_or_existing_pull_secret_changes": False, "automatic_retry": False}
    (folder / "receipt.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))
    print("Private result directory: " + str(folder))
    return 0 if summary["status"].startswith("PASS_") else 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--resume-from", type=Path, required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--cloud-api", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    require(args.resume_from.resolve().parent == root, "PRIOR_RESULT_DIRECTORY_SCOPE")
    receipt = json.loads((args.resume_from / "receipt.json").read_text())
    require(receipt.get("steps", {}).get("resources", {}).get("status") == "PASS_CHANGED_DEPLOYMENTS_RESOURCE_POOL_ADMISSION", "REUSE_COMPLETED_RESOURCE_STAGE_REQUIRED")
    oc = shutil.which("oc") or str(Path.home() / ".local/share/seokpan/oc-cli-4.20/oc")
    client = Client(oc, args.kubeconfig)
    require(client.run(["whoami", "--show-server"]).decode().strip().rstrip("/") == args.cloud_api.rstrip("/"), "CLOUD_TARGET_CHANGED")
    folder = Path(tempfile.mkdtemp(prefix="cloud-native-pull-", dir=root))
    return execute(client, folder)


if __name__ == "__main__":
    raise SystemExit(main())
