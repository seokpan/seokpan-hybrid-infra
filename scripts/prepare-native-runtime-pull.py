"""Detach the known temporary ECR reference after accepted secret-free probes."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

NS = "seokpan-cloud"
SA = "seokpan-cloud-runtime"
TEMP_SECRET = "seokpan-cloud-ecr-pull"


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def without_temporary(refs):
    require(isinstance(refs, list) and all(isinstance(ref, dict) and isinstance(ref.get("name"), str) for ref in refs), "PULL_REFERENCE_FORMAT")
    return [copy.deepcopy(ref) for ref in refs if ref["name"] != TEMP_SECRET]


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--native-proof", type=Path, required=True)
    parser.add_argument("--held-bundle", type=Path, required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--cloud-api", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    folder = Path(tempfile.mkdtemp(prefix="cloud-runtime-native-", dir=root))
    phase, patch_attempted = "SAVED_EVIDENCE", False
    try:
        for path in (args.native_proof, args.held_bundle, args.kubeconfig):
            require(path.resolve().is_relative_to(root), "INPUT_OUTSIDE_ACCEPTED_ROOT")
        proof = json.loads(args.native_proof.read_text())
        require(proof.get("status") == "PASS_SECRET_FREE_WORKER_PULL_AND_EXECUTION" and proof.get("cleanup") == "PASS_OWN_PODS_AND_SERVICEACCOUNT_REMOVED", "ACCEPTED_PULL_AND_CLEANUP_REQUIRED")
        require({(row.get("repository"), row.get("compute_node_number")) for row in proof.get("results", []) if row.get("succeeded") is True} == {(repo, number) for repo in ("backend", "frontend") for number in (1, 2, 3)}, "SIX_ACCEPTED_PROBES_REQUIRED")
        bundle = json.loads(args.held_bundle.read_text())
        candidate = copy.deepcopy(bundle)
        deployments, changed = [], 0
        for obj in candidate["items"]:
            if obj.get("kind") != "Deployment":
                continue
            require(obj["metadata"].get("namespace") == NS and obj["metadata"]["name"] in ("backend", "frontend") and obj["spec"].get("replicas") == 0, "HELD_ZERO_REPLICA_TARGET_REQUIRED")
            pod = obj["spec"]["template"]["spec"]
            require(pod.get("serviceAccountName") == SA, "RUNTIME_SERVICE_ACCOUNT_CHANGED")
            if "imagePullSecrets" in pod:
                old = pod["imagePullSecrets"]
                new = without_temporary(old)
                if new != old:
                    pod["imagePullSecrets"] = new
                    changed += 1
            deployments.append(obj["metadata"]["name"])
        require(sorted(deployments) == ["backend", "frontend"], "TWO_HELD_DEPLOYMENTS_REQUIRED")
        encoded = json.dumps(candidate, indent=2)
        file = folder / "held-native-pull.DO_NOT_APPLY.json"
        file.write_text(encoded)
        prefix = ["oc", "--kubeconfig=" + str(args.kubeconfig), "--request-timeout=25s"]

        def oc(*arguments):
            result = subprocess.run(prefix + list(arguments), capture_output=True, timeout=40)
            require(result.returncode == 0, "COMMAND_FAILED_NO_RAW_OUTPUT")
            return result.stdout.decode()

        phase = "CURRENT_RUNTIME_SERVICE_ACCOUNT"
        require(oc("whoami", "--show-server").strip().rstrip("/") == args.cloud_api.rstrip("/"), "WRONG_CLOUD_TARGET")
        sa = json.loads(oc("get", "serviceaccount", SA, "-n", NS, "-o", "json"))
        meta = sa["metadata"]
        require(not (meta.get("annotations") or {}).get("argocd.argoproj.io/tracking-id") and not (meta.get("labels") or {}).get("app.kubernetes.io/instance"), "SERVICE_ACCOUNT_GITOPS_OWNER_REVIEW_REQUIRED")
        (folder / "serviceaccount-before.json").write_text(json.dumps(sa, indent=2))
        old = sa.get("imagePullSecrets") or []
        new = without_temporary(old)
        if args.apply and old != new:
            phase = "DETACH_KNOWN_TEMPORARY_REFERENCE"
            patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
                     {"op": "test", "path": "/imagePullSecrets", "value": old},
                     {"op": "replace", "path": "/imagePullSecrets", "value": new}]
            patch_file = folder / "serviceaccount-patch.json"
            patch_file.write_text(json.dumps(patch))
            patch_attempted = True
            oc("patch", "serviceaccount", SA, "-n", NS, "--type=json", "--patch-file", str(patch_file))
        if args.apply:
            phase = "RUNTIME_REFERENCE_READBACK"
            after = json.loads(oc("get", "serviceaccount", SA, "-n", NS, "-o", "json"))
            require(after["metadata"]["uid"] == meta["uid"] and all(ref.get("name") != TEMP_SECRET for ref in after.get("imagePullSecrets") or []), "RUNTIME_REFERENCE_NOT_CONFIRMED")
        result = {"status": "PASS_CLOUD_RUNTIME_TEMPORARY_PULL_REFERENCE_DETACHED" if args.apply else "PREPARED_NATIVE_RUNTIME_REFERENCE_NOT_APPLIED",
                  "service_account_patch_attempted": patch_attempted, "candidate_deployments_changed": changed,
                  "candidate_file": str(file), "candidate_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                  "temporary_secret_deleted": False, "other_pull_references_preserved": True,
                  "existing_pull_tests_repeated": False, "resource_admission_repeated": False,
                  "application_replicas": 0, "application_started": False, "sql_calls": 0,
                  "pool_runtime_adopted": False, "actual_application_pull_after_detachment": "NOT_RUN"}
        (folder / "receipt.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 0
    except Exception as error:
        result = {"status": "STOP_NATIVE_RUNTIME_REFERENCE", "phase": phase,
                  "reason": str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                  "service_account_patch_attempted": patch_attempted, "automatic_retry": False,
                  "sql_calls": 0, "application_started": False}
        (folder / "receipt.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 1
    finally:
        print("Private result directory: " + str(folder))


if __name__ == "__main__":
    raise SystemExit(main())
