"""Register a pinned monitoring Application and namespace-scoped controller access."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

NS = "seokpan-cloud"
ARGO_NS = "openshift-gitops"
PROJECT = "seokpan-cloud-monitoring"
SA = "openshift-gitops-argocd-application-controller"
ROLE_NAME = "seokpan-cloud-monitoring-controller"
GROUP = "seokpan-cloud-standby-operators"
HUMAN = "cyj200115-prog"
ARGO_ROLE = "role:seokpan-cloud-monitoring-operator"
REPO = "https://github.com/seokpan/seokpan-hybrid-gitops.git"
REVISION = "16fd14cd1c085a8314a5e2e18b89e2cd3b051626"
SOURCE_HASHES = {
    "project/application.yaml": "86bd5647bfd848369ad76791c992b7bf6e1141a5ff3ca0d057bd91f5eaab672c",
    "project/appproject.yaml": "4c08b83c2d679b8c0c7801013ee80f938f3d3a26a209d3d93ab95b11b1de3b4a",
    "app/kustomization.yaml": "28c3ff1685d73662344e9500d60dfe1788872cd08ed526c6d304cf1391cabc80",
    "app/networkpolicy-uwm-to-backend.yaml": "1bd7f4f2455364a523aea18f17c6c350d8ea44202e4aa273b3b8ff11571cf7db",
    "app/prometheusrule-availability.yaml": "b624c34d3b29f582fbfeb005e3f8015e26490a6899c6c9c267149da1e185fb69",
    "app/servicemonitor-backend.yaml": "5d056aacf858d17b5adeaabefaedf4de0d80e337050438a97769e8acd4862e39",
}
VERBS = ["get", "list", "watch", "create", "update", "patch"]
LINES = [f"p, {ARGO_ROLE}, projects, get, {PROJECT}, allow",
         f"p, {ARGO_ROLE}, applications, get, {PROJECT}/*, allow",
         f"p, {ARGO_ROLE}, applications, sync, {PROJECT}/*, allow",
         f"g, {GROUP}, {ARGO_ROLE}"]
OWNER = "seokpan.io/bootstrap-owner"


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def role_objects():
    meta = {"name": ROLE_NAME, "namespace": NS}
    role = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": meta,
            "rules": [{"apiGroups": [g], "resources": r, "verbs": VERBS}
                      for g, r in [("monitoring.coreos.com", ["servicemonitors", "prometheusrules"]),
                                   ("networking.k8s.io", ["networkpolicies"])]]}
    binding = {"apiVersion": role["apiVersion"], "kind": "RoleBinding", "metadata": copy.deepcopy(meta),
               "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": ROLE_NAME},
               "subjects": [{"kind": "ServiceAccount", "name": SA, "namespace": ARGO_NS}]}
    return [role, binding]


def validate_sources(project, application):
    project = copy.deepcopy(project)
    application = copy.deepcopy(application)
    for item, kind in ((project, "AppProject"), (application, "Application")):
        need(item.get("kind") == kind and item["metadata"].get("name") == PROJECT
             and item["metadata"].get("namespace") == ARGO_NS, "PINNED_PROJECT_IDENTITY_CHANGED")
    expected = {"sourceRepos": [REPO], "destinations": [{"server": "https://kubernetes.default.svc", "namespace": NS}],
                "clusterResourceWhitelist": [], "clusterResourceBlacklist": [{"group": "*", "kind": "*"}],
                "namespaceResourceWhitelist": [{"group": "monitoring.coreos.com", "kind": "ServiceMonitor"},
                                               {"group": "monitoring.coreos.com", "kind": "PrometheusRule"},
                                               {"group": "networking.k8s.io", "kind": "NetworkPolicy"}]}
    need(project["spec"] == expected, "MONITORING_PROJECT_SCOPE_CHANGED")
    expected_app = {"project": PROJECT,
                    "source": {"repoURL": REPO, "targetRevision": "GITOPS_REVISION_INPUT_REQUIRED", "path": "platform/rosa-monitoring/app"},
                    "destination": {"server": "https://kubernetes.default.svc", "namespace": NS},
                    "syncPolicy": {"syncOptions": ["FailOnSharedResource=true", "Prune=false", "Delete=false"]}}
    need(application["spec"] == expected_app, "MANUAL_MONITORING_APPLICATION_SCOPE_CHANGED")
    application["spec"]["source"]["targetRevision"] = REVISION
    return project, application


def scoped_policy(original):
    rows = [line.strip() for line in original.splitlines() if line.strip()]
    need(all(ARGO_ROLE not in line or line in LINES for line in rows), "MONITORING_ROLE_HAS_UNREVIEWED_POLICY")
    missing = [line for line in LINES if line not in rows]
    if not missing:
        return original
    return original.rstrip() + ("\n" if original.strip() and missing else "") + "\n".join(missing)


def owned_same(current, desired):
    need((current["metadata"].get("annotations") or {}).get(OWNER) == "rosa-monitoring", "EXISTING_RESOURCE_OWNER_REVIEW_REQUIRED")
    need(not current["metadata"].get("ownerReferences") and not (current["metadata"].get("annotations") or {}).get("argocd.argoproj.io/tracking-id"), "EXTERNAL_RESOURCE_OWNER_REVIEW_REQUIRED")
    for key in ("spec", "rules", "roleRef", "subjects"):
        if key in desired:
            need(current.get(key) == desired[key], "EXISTING_MONITORING_RESOURCE_DRIFT")
    need(not current.get("operation"), "MONITORING_OPERATION_ALREADY_RUNNING")


def bootstrap_tasks(prefix, missing, deferred):
    tasks = []
    for number, file in enumerate(missing):
        if file in deferred:
            tasks.append({"name": f"Validate dependent bootstrap object {number}",
                          "ansible.builtin.command": {"argv": prefix + ["create", "--dry-run=server", "-f", file, "-o", "name"]},
                          "changed_when": False})
        tasks.append({"name": f"Create absent bootstrap object {number}",
                      "ansible.builtin.command": {"argv": prefix + ["create", "-f", file, "-o", "name"]},
                      "changed_when": True})
    return tasks


class Runner:
    def __init__(self, oc, kubeconfig, folder):
        self.prefix = [oc, "--kubeconfig=" + str(kubeconfig), "--request-timeout=25s"]
        self.folder = folder
        self.calls = 0

    def run(self, *args, allow_no=False):
        self.calls += 1
        result = subprocess.run(self.prefix + list(args), capture_output=True, timeout=40)
        if result.returncode and not (allow_no and result.stdout.strip() == b"no" and not result.stderr.strip()):
            (self.folder / f"command-{self.calls}.stderr").write_bytes(result.stderr)
            text = result.stderr.decode(errors="replace").lower()
            code = "ACCESS_DENIED" if "forbidden" in text else "AUTHENTICATION_REQUIRED" if "unauthorized" in text or "provide credentials" in text else "API_COMMAND_FAILED"
            raise RuntimeError(code)
        return result.stdout.decode()

    def get(self, kind, name, namespace=None):
        args = ["get", kind, name, "--ignore-not-found=true", "-o", "json"]
        if namespace:
            args += ["-n", namespace]
        text = self.run(*args)
        return json.loads(text) if text.strip() else None


def connection_from_native_log(root, log, fallback=None):
    need(log.resolve().is_relative_to(root), "NATIVE_LOG_OUTSIDE_ACCEPTED_ROOT")
    rows = []
    folder = None
    for line in log.read_text().splitlines():
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        if line.startswith("Private result directory: "):
            folder = Path(line.partition(": ")[2])
    need(rows and folder and folder.resolve().is_relative_to(root), "NATIVE_RESULT_REQUIRED")
    result = rows[-1]
    need(result.get("status") == "PASS_CLOUD_RUNTIME_TEMPORARY_PULL_REFERENCE_DETACHED"
         or result.get("personal_user") == "tjung03", "SHARED_PERSONAL_LOGIN_NOT_ACCEPTED")
    kubeconfig = Path(result.get("kubeconfig_file", str(folder / "personal-kubeconfig")))
    if not kubeconfig.is_file() and fallback is not None:
        kubeconfig = fallback
    need(kubeconfig.resolve().is_relative_to(root) and kubeconfig.is_file(), "ACCEPTED_PERSONAL_KUBECONFIG_REQUIRED")
    return kubeconfig


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--native-run-log", type=Path, required=True)
    parser.add_argument("--cloud-api", required=True)
    parser.add_argument("--fallback-kubeconfig", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    folder = Path(tempfile.mkdtemp(prefix="cloud-monitoring-registration-", dir=root))
    phase = "ACCEPTED_PERSONAL_CONNECTION"
    create_attempted, policy_patch_attempted = False, False
    objects = []
    runner = None
    result = {}
    try:
        kubeconfig = connection_from_native_log(root, args.native_run_log, args.fallback_kubeconfig)
        oc = shutil.which("oc") or str(Path.home() / ".local/share/seokpan/oc-cli-4.20/oc")
        need(Path(oc).is_file(), "EXISTING_OC_REQUIRED")
        need(shutil.which("ansible-playbook"), "EXISTING_ANSIBLE_REQUIRED_NO_INSTALL_ATTEMPTED")
        import fcntl
        lock = (root / "cloud-continuation.lock").open("a")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        runner = Runner(oc, kubeconfig, folder)
        need(runner.run("whoami", "--show-server").strip().rstrip("/") == args.cloud_api.rstrip("/"), "WRONG_CLOUD_TARGET")
        need(runner.run("whoami").strip() == "tjung03", "PERSONAL_TJUNG03_LOGIN_REQUIRED")
        phase = "PINNED_MONITORING_SOURCE"
        source = folder / "source"
        for path, expected in SOURCE_HASHES.items():
            url = f"https://raw.githubusercontent.com/seokpan/seokpan-hybrid-gitops/{REVISION}/platform/rosa-monitoring/{path}"
            with urllib.request.urlopen(url, timeout=20) as response:
                raw = response.read(1024 * 1024)
            need(hashlib.sha256(raw).hexdigest() == expected, "PINNED_MONITORING_SOURCE_CHECKSUM")
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        project, app = [json.loads(runner.run("create", "--dry-run=client", "-f", str(source / path), "-o", "json"))
                        for path in ("project/appproject.yaml", "project/application.yaml")]
        project, app = validate_sources(project, app)
        objects = role_objects() + [project, app]
        phase = "PLATFORM_AND_PERMISSION_PREFLIGHT"
        controller = runner.get("statefulset", "openshift-gitops-application-controller", ARGO_NS)
        need(controller and controller["spec"]["template"]["spec"].get("serviceAccountName") == SA, "CONTROLLER_SERVICE_ACCOUNT_CHANGED")
        group = runner.get("group", GROUP)
        need(group and set(group.get("users") or []) == {HUMAN}, "MONITORING_RECIPIENT_GROUP_REVIEW_REQUIRED")
        argo = runner.get("argocd", "openshift-gitops", ARGO_NS)
        need(argo and argo["spec"].get("rbac", {}).get("scopes") in ("[groups]", ["groups"]), "ARGO_GROUP_SCOPE_REQUIRED")
        old_policy = argo["spec"]["rbac"].get("policy", "")
        new_policy = scoped_policy(old_policy)
        patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": argo["metadata"]["resourceVersion"]},
                 {"op": "test", "path": "/spec/rbac/policy", "value": old_policy},
                 {"op": "replace", "path": "/spec/rbac/policy", "value": new_policy}]
        patch_file = folder / "argo-monitoring-policy-patch.json"
        patch_file.write_text(json.dumps(patch))
        missing, deferred, existing = [], [], {}
        for number, item in enumerate(objects):
            item["metadata"].setdefault("annotations", {})[OWNER] = "rosa-monitoring"
            current = runner.get(item["kind"], item["metadata"]["name"], item["metadata"]["namespace"])
            existing[item["kind"]] = current is not None
            if current:
                owned_same(current, item)
            else:
                file = folder / f"object-{number}.json"
                file.write_text(json.dumps(item))
                depends_on_absent_object = ((item["kind"] == "RoleBinding" and not existing.get("Role"))
                                           or (item["kind"] == "Application" and not existing.get("AppProject")))
                if depends_on_absent_object:
                    deferred.append(str(file))
                else:
                    runner.run("create", "--dry-run=server", "-f", str(file), "-o", "name")
                missing.append(str(file))
        if new_policy != old_policy:
            runner.run("patch", "argocd", "openshift-gitops", "-n", ARGO_NS, "--type=json", "--patch-file", str(patch_file), "--dry-run=server", "-o", "name")
        rendered = runner.run("kustomize", str(source / "app"))
        render_file = folder / "monitoring-render.DO_NOT_SYNC.yaml"
        render_file.write_text(rendered)
        runner.run("apply", "--dry-run=server", "-n", NS, "-f", str(render_file), "-o", "name")
        if args.apply:
            phase = "ANSIBLE_MONITORING_REGISTRATION"
            if missing:
                playbook = [{"name": "Register the pinned manual monitoring application", "hosts": "localhost", "connection": "local", "gather_facts": False,
                             "tasks": bootstrap_tasks(runner.prefix, missing, deferred)}]
                play_file = folder / "monitoring-bootstrap.json"
                play_file.write_text(json.dumps(playbook))
                create_attempted = True
                with (folder / "ansible.log").open("wb") as log:
                    run = subprocess.run(["ansible-playbook", "-i", "localhost,", str(play_file)], stdout=log, stderr=subprocess.STDOUT, timeout=180)
                need(run.returncode == 0, "ANSIBLE_FAILED_PARTIAL_STATE_PRESERVED")
            phase = "SCOPED_MONITORING_ARGO_POLICY"
            if new_policy != old_policy:
                latest = runner.get("argocd", "openshift-gitops", ARGO_NS)
                need(latest and latest["spec"].get("rbac") == argo["spec"].get("rbac"), "ARGO_RBAC_CHANGED_DURING_REGISTRATION")
                patch[0]["value"] = latest["metadata"]["resourceVersion"]
                patch_file.write_text(json.dumps(patch))
                policy_patch_attempted = True
                runner.run("patch", "argocd", "openshift-gitops", "-n", ARGO_NS, "--type=json", "--patch-file", str(patch_file), "-o", "name")
            phase = "MONITORING_REGISTRATION_READBACK"
            for item in objects:
                current = runner.get(item["kind"], item["metadata"]["name"], item["metadata"]["namespace"])
                need(current, "REGISTERED_OBJECT_NOT_READABLE")
                owned_same(current, item)
            checks = []
            for resource in ("servicemonitors.monitoring.coreos.com", "prometheusrules.monitoring.coreos.com", "networkpolicies.networking.k8s.io"):
                for action in VERBS:
                    answer = runner.run("auth", "can-i", action, resource, "-n", NS, "--as=system:serviceaccount:" + ARGO_NS + ":" + SA, allow_no=True).strip().lower()
                    checks.append(answer == "yes")
            need(all(checks), "CONTROLLER_MONITORING_PERMISSION_NOT_CONFIRMED")
            phase = "ARGO_MONITORING_POLICY_RECONCILIATION"
            for attempt in range(21):
                cm = runner.get("configmap", "argocd-rbac-cm", ARGO_NS)
                policy = (cm or {}).get("data", {}).get("policy.csv", "")
                if set(LINES) <= {line.strip() for line in policy.splitlines()}:
                    break
                if attempt % 5 == 0:
                    print(json.dumps({"status": "WAITING_MONITORING_ARGO_POLICY"}), flush=True)
                time.sleep(3)
            else:
                raise RuntimeError("ARGO_MONITORING_POLICY_NOT_RECONCILED")
        result = {"status": "PASS_MONITORING_REGISTERED_MANUAL_SYNC_PENDING" if args.apply else "MONITORING_REGISTRATION_PREFLIGHT_ONLY",
                  "project": PROJECT, "application": PROJECT, "namespace": NS, "argo_namespace": ARGO_NS,
                  "controller_service_account": SA, "source_revision": REVISION,
                  "controller_checks": 18 if args.apply else 0, "registration_method": "MINIMAL_ANSIBLE_BOOTSTRAP",
                  "dependent_admission_checks": len(deferred),
                  "D_actions": ["get", "sync"], "D_browser_acceptance": "NOT_VERIFIED",
                  "create_attempted": create_attempted, "argo_policy_patch_attempted": policy_patch_attempted,
                  "automatic_sync": False, "sync_started": False, "monitoring_resources_synced": False,
                  "application_started": False, "migration_started": False, "sql_calls": 0, "existing_runtime_tests_repeated": False}
        return_code = 0
    except Exception as error:
        result = {"status": "STOP_MONITORING_REGISTRATION", "phase": phase,
                  "reason": str(error) if isinstance(error, RuntimeError) else type(error).__name__,
                  "create_attempted": create_attempted, "argo_policy_patch_attempted": policy_patch_attempted,
                  "automatic_retry": False, "sync_started": False, "application_started": False, "sql_calls": 0}
        return_code = 1
    result["oc_commands"] = runner.calls if runner else 0
    (folder / "receipt.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    print("Private monitoring registration directory: " + str(folder))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
