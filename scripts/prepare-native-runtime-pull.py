"""Detach the known temporary ECR reference after accepted secret-free probes."""
import argparse
import copy
import getpass
import hashlib
import json
import os
import shlex
import shutil
from pathlib import Path
import subprocess
import tempfile
import sys

NS = "seokpan-cloud"
SA = "seokpan-cloud-runtime"
TEMP_SECRET = "seokpan-cloud-ecr-pull"


def token_from_input(value, api):
    value = value.strip()
    if not any(char.isspace() for char in value):
        require(bool(value), "EMPTY_PERSONAL_TOKEN")
        return value
    try:
        words = shlex.split(value)
    except ValueError:
        raise RuntimeError("PERSONAL_LOGIN_INPUT_FORMAT") from None
    require(len(words) >= 3 and Path(words[0]).name in ("oc", "oc.exe") and words[1] == "login", "PASTE_API_TOKEN_OR_OC_LOGIN_COMMAND")
    fields = {}
    index = 2
    while index < len(words):
        key, separator, item = words[index].partition("=")
        require(key in ("--token", "--server") and key not in fields, "LOGIN_COMMAND_OPTIONS_NOT_ACCEPTED")
        if not separator:
            index += 1
            require(index < len(words), "PERSONAL_LOGIN_INPUT_FORMAT")
            item = words[index]
        fields[key] = item
        index += 1
    require(fields.get("--server", "").rstrip("/") == api.rstrip("/"), "TOKEN_COMMAND_WRONG_CLOUD_SERVER")
    token = fields.get("--token", "")
    require(bool(token) and not any(char.isspace() for char in token), "INVALID_PERSONAL_TOKEN_FORMAT")
    return token


def command_failure(stderr):
    text = stderr.decode(errors="replace").lower()
    for code, patterns in (
        ("AUTHENTICATION_REQUIRED", ("unauthorized", "you must be logged in", "provide credentials", "token has expired")),
        ("ACCESS_DENIED", ("forbidden",)),
        ("TLS_TRUST_FAILED", ("x509:", "certificate signed by unknown", "certificate has expired")),
        ("API_UNREACHABLE", ("no such host", "connection refused", "i/o timeout", "network is unreachable", "tls handshake timeout")),
        ("SERVICE_ACCOUNT_NOT_FOUND", ("(notfound)", "not found")),
    ):
        if any(value in text for value in patterns):
            return code
    return "API_COMMAND_FAILED"


def personal_config(document, api, token):
    require(bool(token) and not any(char.isspace() for char in token), "INVALID_PERSONAL_TOKEN_FORMAT")
    contexts = {item["name"]: item["context"] for item in document.get("contexts", [])}
    context = contexts.get(document.get("current-context"), {})
    matches = [item["cluster"] for item in document.get("clusters", []) if item["name"] == context.get("cluster")]
    require(len(matches) == 1, "SINGLE_CURRENT_CLUSTER_REQUIRED")
    cluster = copy.deepcopy(matches[0])
    require(cluster.get("server", "").rstrip("/") == api.rstrip("/") and not cluster.get("insecure-skip-tls-verify"), "VERIFIED_CLOUD_TRUST_REQUIRED")
    return {"apiVersion": "v1", "kind": "Config", "clusters": [{"name": "cloud", "cluster": cluster}],
            "users": [{"name": "personal", "user": {"token": token}}],
            "contexts": [{"name": "personal-cloud", "context": {"cluster": "cloud", "user": "personal", "namespace": NS}}],
            "current-context": "personal-cloud"}


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
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--reauthenticate-if-required", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    folder = Path(tempfile.mkdtemp(prefix="cloud-runtime-native-", dir=root))
    phase, patch_attempted = "SAVED_EVIDENCE", False
    diagnostic = None
    actual_personal_user = None
    auth_refreshed = False
    active_kubeconfig = args.kubeconfig
    try:
        if args.resume_from:
            require(args.resume_from.resolve().is_relative_to(root), "RESUME_OUTSIDE_ACCEPTED_ROOT")
            previous = json.loads((args.resume_from / "receipt.json").read_text())
            require(previous.get("status") == "STOP_NATIVE_RUNTIME_REFERENCE" and previous.get("service_account_patch_attempted") is False, "RESUME_REQUIRES_CONFIRMED_NO_PATCH")
            saved_personal = args.resume_from / "personal-kubeconfig"
            if saved_personal.is_file():
                require(saved_personal.resolve().is_relative_to(root), "SAVED_LOGIN_OUTSIDE_ACCEPTED_ROOT")
                personal_config(json.loads(saved_personal.read_text()), args.cloud_api, "VALIDATION_ONLY")
                active_kubeconfig = saved_personal
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
        executable = shutil.which("oc") or str(Path.home() / ".local/share/seokpan/oc-cli-4.20/oc")
        require(Path(executable).is_file(), "OC_EXECUTABLE_NOT_FOUND")
        prefix = [executable, "--kubeconfig=" + str(active_kubeconfig), "--request-timeout=25s"]

        def oc(*arguments):
            nonlocal diagnostic, auth_refreshed, active_kubeconfig, phase, actual_personal_user
            result = subprocess.run(prefix + list(arguments), capture_output=True, timeout=40)
            if result.returncode:
                (folder / "failed-command.stderr").write_bytes(result.stderr)
                diagnostic = {"operation": phase, "exit_code": result.returncode, "classification": command_failure(result.stderr), "raw_output": "PRIVATE_NOT_PRINTED"}
                if (diagnostic["classification"] == "AUTHENTICATION_REQUIRED" and args.reauthenticate_if_required
                        and not auth_refreshed and not patch_attempted
                        and phase in ("CURRENT_CLUSTER_IDENTITY", "CURRENT_RUNTIME_SERVICE_ACCOUNT", "PERSONAL_IDENTITY_VERIFICATION")):
                    require(sys.stdin.isatty(), "INTERACTIVE_PERSONAL_LOGIN_REQUIRED")
                    auth_refreshed = True
                    config = subprocess.run(prefix + ["config", "view", "--raw", "--flatten", "-o", "json"], capture_output=True, timeout=40)
                    require(config.returncode == 0, "EXISTING_CLUSTER_TRUST_READ_FAILED")
                    print("PERSONAL_LOGIN_REQUIRED: Use this ROSA console > tjung03 > Copy login command > seokpan-github > Display token. Paste the API token or the complete oc login command below; it will not be executed as shell text. Do not use an OAuth App secret or a Red Hat offline token.", flush=True)
                    token = token_from_input(getpass.getpass("tjung03 ROSA API token or oc login command (hidden): "), args.cloud_api)
                    personal = personal_config(json.loads(config.stdout), args.cloud_api, token)
                    active_kubeconfig = folder / "personal-kubeconfig"
                    active_kubeconfig.write_text(json.dumps(personal))
                    prefix[1] = "--kubeconfig=" + str(active_kubeconfig)
                    original_phase = phase
                    phase = "PERSONAL_IDENTITY_VERIFICATION"
                    identity = subprocess.run(prefix + ["whoami"], capture_output=True, timeout=40)
                    if identity.returncode:
                        (folder / "personal-whoami.stderr").write_bytes(identity.stderr)
                        diagnostic = {"operation": phase, "exit_code": identity.returncode, "classification": command_failure(identity.stderr), "raw_output": "PRIVATE_NOT_PRINTED"}
                        raise RuntimeError("PERSONAL_LOGIN_" + diagnostic["classification"])
                    actual_personal_user = identity.stdout.decode().strip()
                    require(actual_personal_user == "tjung03", "PERSONAL_USER_MISMATCH")
                    phase = original_phase
                    return oc(*arguments)
                raise RuntimeError(diagnostic["classification"])
            return result.stdout.decode()

        phase = "CURRENT_CLUSTER_IDENTITY"
        require(oc("whoami", "--show-server").strip().rstrip("/") == args.cloud_api.rstrip("/"), "WRONG_CLOUD_TARGET")
        if active_kubeconfig != args.kubeconfig:
            phase = "PERSONAL_IDENTITY_VERIFICATION"
            actual_personal_user = oc("whoami").strip()
            require(actual_personal_user == "tjung03", "PERSONAL_USER_MISMATCH")
        phase = "CURRENT_RUNTIME_SERVICE_ACCOUNT"
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
        result.update({"authentication_refresh_attempted": auth_refreshed, "kubeconfig_file": str(active_kubeconfig), "initial_admin_credentials_changed": False})
        (folder / "receipt.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 0
    except Exception as error:
        result = {"status": "STOP_NATIVE_RUNTIME_REFERENCE", "phase": phase,
                  "reason": str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                  "service_account_patch_attempted": patch_attempted, "automatic_retry": False,
                  "diagnostic": diagnostic,
                  "authentication_refresh_attempted": auth_refreshed,
                  "personal_user": actual_personal_user if actual_personal_user in ("tjung03", "cluster-admin", "system:anonymous") else ("OTHER_IDENTITY" if actual_personal_user else None),
                  "sql_calls": 0, "application_started": False}
        (folder / "receipt.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 1
    finally:
        print("Private result directory: " + str(folder))


if __name__ == "__main__":
    raise SystemExit(main())
