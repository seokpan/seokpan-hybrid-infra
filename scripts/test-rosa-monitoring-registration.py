"""Offline scope and failure checks for manual monitoring bootstrap."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import contextlib
import io
import sys
import types

spec = importlib.util.spec_from_file_location("monitoring_bootstrap", Path(__file__).with_name("register-rosa-monitoring.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def declarations():
    project = {"kind": "AppProject", "metadata": {"name": m.PROJECT, "namespace": m.ARGO_NS},
               "spec": {"sourceRepos": [m.REPO], "destinations": [{"server": "https://kubernetes.default.svc", "namespace": m.NS}],
                        "clusterResourceWhitelist": [], "clusterResourceBlacklist": [{"group": "*", "kind": "*"}],
                        "namespaceResourceWhitelist": [{"group": "monitoring.coreos.com", "kind": "ServiceMonitor"},
                                                       {"group": "monitoring.coreos.com", "kind": "PrometheusRule"},
                                                       {"group": "networking.k8s.io", "kind": "NetworkPolicy"}]}}
    app = {"kind": "Application", "metadata": {"name": m.PROJECT, "namespace": m.ARGO_NS},
           "spec": {"project": m.PROJECT, "source": {"repoURL": m.REPO, "targetRevision": "GITOPS_REVISION_INPUT_REQUIRED", "path": "platform/rosa-monitoring/app"},
                    "destination": {"server": "https://kubernetes.default.svc", "namespace": m.NS},
                    "syncPolicy": {"syncOptions": ["FailOnSharedResource=true", "Prune=false", "Delete=false"]}}}
    return project, app


def stopped_registration(root):
    previous = root / "stopped"
    previous.mkdir()
    (previous / "receipt.json").write_text(json.dumps({"status": "STOP_MONITORING_REGISTRATION",
        "phase": "MONITORING_REGISTRATION_READBACK", "create_attempted": True,
        "argo_policy_patch_attempted": True, "sync_started": False, "application_started": False, "sql_calls": 0}))
    project, app = declarations()
    project, app = m.validate_sources(project, app)
    objects = m.role_objects() + [project, app]
    for number, item in enumerate(objects):
        item["metadata"]["annotations"] = {m.OWNER: "rosa-monitoring"}
        (previous / f"object-{number}.json").write_text(json.dumps(item))
    return previous, objects


class ReviewRunner:
    def __init__(self, *, allowed=True, evaluation_error="", capabilities=("yes",)):
        self.allowed, self.error = allowed, evaluation_error
        self.capabilities = iter(capabilities)
        self.commands, self.requests = [], []
        self.calls = 0

    def run(self, *args, **kwargs):
        self.calls += 1
        self.commands.append(args)
        if args[:2] == ("auth", "can-i"):
            return next(self.capabilities)
        self.requests.append(json.loads(Path(args[-1]).read_text()))
        status = {"allowed": self.allowed, "evaluationError": self.error}
        return json.dumps({"status": status} if self.requests[-1]["apiVersion"] == "authorization.k8s.io/v1" else status)


class MonitoringTests(unittest.TestCase):
    def test_pin_is_injected_without_modifying_source_or_enabling_sync(self):
        project, app = declarations()
        _, pinned = m.validate_sources(project, app)
        self.assertEqual(pinned["spec"]["source"]["targetRevision"], m.REVISION)
        self.assertEqual(app["spec"]["source"]["targetRevision"], "GITOPS_REVISION_INPUT_REQUIRED")
        self.assertNotIn("automated", pinned["spec"]["syncPolicy"])

    def test_auto_sync_application_or_migration_path_is_rejected(self):
        for variant in ("sync", "path", "namespace", "repository"):
            project, app = declarations()
            if variant == "sync":
                app["spec"]["syncPolicy"]["automated"] = {}
            elif variant == "path":
                app["spec"]["source"]["path"] = "apps/cloud"
            elif variant == "namespace":
                app["spec"]["destination"]["namespace"] = "elsewhere"
            else:
                app["spec"]["source"]["repoURL"] = "https://elsewhere"
            with self.assertRaises(RuntimeError):
                m.validate_sources(project, app)

    def test_project_cannot_allow_deployments_secrets_or_wildcards(self):
        for kind in ("Deployment", "Secret", "*"):
            project, app = declarations()
            project["spec"]["namespaceResourceWhitelist"].append({"group": "*", "kind": kind})
            with self.assertRaises(RuntimeError):
                m.validate_sources(project, app)

    def test_controller_role_is_namespace_scoped_and_has_no_delete_or_secret_access(self):
        role, binding = m.role_objects()
        self.assertEqual(role["metadata"]["namespace"], m.NS)
        self.assertEqual({r for rule in role["rules"] for r in rule["resources"]}, {"servicemonitors", "prometheusrules", "networkpolicies"})
        self.assertTrue(all("delete" not in rule["verbs"] and "*" not in rule["verbs"] for rule in role["rules"]))
        self.assertEqual(binding["subjects"], [{"kind": "ServiceAccount", "name": m.SA, "namespace": m.ARGO_NS}])

    def test_policy_preserves_app_grants_and_only_adds_monitoring_get_sync(self):
        original = "g, existing, role:existing\np, role:existing, applications, sync, seokpan-cloud-app/*, allow\n"
        result = m.scoped_policy(original)
        self.assertTrue(result.startswith(original))
        self.assertEqual(set(result.splitlines()) - set(original.splitlines()), set(m.LINES))
        self.assertNotIn(", delete,", "\n".join(m.LINES))
        self.assertNotIn(", create,", "\n".join(m.LINES))

    def test_repeated_policy_does_not_create_a_patch_for_newline_only_change(self):
        original = "\n".join(m.LINES) + "\n"
        self.assertEqual(m.scoped_policy(original), original)

    def test_wider_preexisting_monitoring_role_policy_requires_review(self):
        with self.assertRaises(RuntimeError):
            m.scoped_policy(f"p, {m.ARGO_ROLE}, applications, *, */*, allow")

    def test_external_owner_or_drift_is_not_overwritten(self):
        desired = m.role_objects()[0]
        current = copy.deepcopy(desired)
        current["metadata"]["annotations"] = {m.OWNER: "someone-else"}
        with self.assertRaises(RuntimeError):
            m.owned_same(current, desired)
        current["metadata"]["annotations"][m.OWNER] = "rosa-monitoring"
        current["rules"][0]["verbs"].append("delete")
        with self.assertRaises(RuntimeError):
            m.owned_same(current, desired)

    def test_owned_application_with_running_sync_requires_attention(self):
        _, app = declarations()
        app["metadata"]["annotations"] = {m.OWNER: "rosa-monitoring"}
        current = copy.deepcopy(app)
        current["operation"] = {"sync": {}}
        with self.assertRaises(RuntimeError):
            m.owned_same(current, app)

    def test_native_auth_failure_does_not_select_an_unverified_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "native.log"
            log.write_text(json.dumps({"status": "STOP_NATIVE_RUNTIME_REFERENCE", "personal_user": None}) + "\nPrivate result directory: " + str(root / "result"))
            with self.assertRaises(RuntimeError):
                m.connection_from_native_log(root, log)

    def test_native_success_selects_its_exact_protected_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kubeconfig = root / "personal-kubeconfig"
            kubeconfig.write_text("PRIVATE_NOT_READ_BY_SELECTOR")
            log = root / "native.log"
            log.write_text(json.dumps({"status": "PASS_CLOUD_RUNTIME_TEMPORARY_PULL_REFERENCE_DETACHED", "kubeconfig_file": str(kubeconfig)}) + "\nPrivate result directory: " + str(root))
            self.assertEqual(m.connection_from_native_log(root, log), kubeconfig)

    def test_verified_user_can_reuse_fallback_but_path_must_stay_in_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fallback = root / "saved-kubeconfig"
            fallback.write_text("PRIVATE")
            log = root / "native.log"
            log.write_text(json.dumps({"status": "STOP_NATIVE_RUNTIME_REFERENCE", "personal_user": "tjung03"}) + "\nPrivate result directory: " + str(root / "result"))
            self.assertEqual(m.connection_from_native_log(root, log, fallback), fallback)
            with self.assertRaises(RuntimeError):
                m.connection_from_native_log(root / "other", log, fallback)

    def test_dependent_binding_and_application_validate_after_parent_creation(self):
        files = ["role.json", "binding.json", "project.json", "application.json"]
        tasks = m.bootstrap_tasks(["oc"], files, ["binding.json", "application.json"])
        commands = [task["ansible.builtin.command"]["argv"] for task in tasks]
        self.assertEqual(commands[0], ["oc", "create", "-f", "role.json", "-o", "name"])
        self.assertEqual(commands[1], ["oc", "create", "--dry-run=server", "-f", "binding.json", "-o", "name"])
        self.assertEqual(commands[3], ["oc", "create", "-f", "project.json", "-o", "name"])
        self.assertEqual(commands[4], ["oc", "create", "--dry-run=server", "-f", "application.json", "-o", "name"])

    def test_can_i_no_is_a_negative_result_and_impersonation_error_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = m.Runner("oc", Path(directory) / "config", Path(directory))
            with patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"no\n", b"")):
                self.assertEqual(runner.run("auth", "can-i", "create", "servicemonitors", allow_no=True).strip(), "no")
            with patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"Forbidden: cannot impersonate")):
                with self.assertRaisesRegex(RuntimeError, "ACCESS_DENIED"):
                    runner.run("auth", "can-i", "create", "servicemonitors", allow_no=True)

    def test_controller_reviews_include_serviceaccount_groups_without_impersonation(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ReviewRunner()
            api, checks = m.controller_authorization(runner, Path(directory))
            self.assertEqual(api, "authorization.k8s.io")
            self.assertEqual(len(checks), 18)
            self.assertTrue(all(item["pass"] for item in checks))
            for item in runner.requests:
                self.assertEqual(item["spec"]["user"], f"system:serviceaccount:{m.ARGO_NS}:{m.SA}")
                self.assertEqual(item["spec"]["groups"], ["system:serviceaccounts", "system:serviceaccounts:" + m.ARGO_NS, "system:authenticated"])
                self.assertEqual(item["spec"]["resourceAttributes"]["namespace"], m.NS)
            self.assertFalse(any("--as" in str(command) or "token" in command for command in runner.commands))
            self.assertTrue(all(command[1] == "--raw" for command in runner.commands[1:]))

    def test_review_denial_and_evaluation_error_cannot_be_reported_as_pass(self):
        for runner in (ReviewRunner(allowed=False), ReviewRunner(evaluation_error="policy lookup failed")):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(RuntimeError, "CONTROLLER_MONITORING_PERMISSION_NOT_CONFIRMED"):
                    m.controller_authorization(runner, Path(directory))
                checks = json.loads((Path(directory) / "controller-authorization.json").read_text())["checks"]
                self.assertEqual(len(checks), 18)
                self.assertTrue(all(not check["pass"] for check in checks))

    def test_no_review_capability_stops_without_a_privilege_or_token_change(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ReviewRunner(capabilities=("no", "no"))
            with self.assertRaisesRegex(RuntimeError, "NAMESPACE_AUTHORIZATION_REVIEW_NOT_PERMITTED"):
                m.controller_authorization(runner, Path(directory))
            self.assertEqual(len(runner.requests), 0)
            self.assertTrue(all(command[:2] == ("auth", "can-i") for command in runner.commands))

    def test_openshift_review_api_uses_its_own_shape_when_authorized(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ReviewRunner(capabilities=("no", "yes"))
            api, checks = m.controller_authorization(runner, Path(directory))
            self.assertEqual(api, "authorization.openshift.io")
            self.assertEqual(len(checks), 18)
            self.assertTrue(all(item["pass"] for item in checks))
            self.assertTrue(all(item["namespace"] == m.NS and "spec" not in item for item in runner.requests))

    def test_resume_requires_exact_stop_and_saved_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, objects = stopped_registration(root)
            self.assertEqual(len(m.resume_objects(root, previous)), 4)
            objects[0]["rules"][0]["verbs"].append("delete")
            (previous / "object-0.json").write_text(json.dumps(objects[0]))
            with self.assertRaisesRegex(RuntimeError, "EXISTING_MONITORING_RESOURCE_DRIFT"):
                m.resume_objects(root, previous)
            with self.assertRaisesRegex(RuntimeError, "PREVIOUS_REGISTRATION_OUTSIDE_ROOT"):
                m.resume_objects(root / "elsewhere", previous)

    def test_resume_rejects_receipts_before_registration_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, _ = stopped_registration(root)
            file = previous / "receipt.json"
            receipt = json.loads(file.read_text())
            for key, value in (("phase", "ANSIBLE_MONITORING_REGISTRATION"), ("create_attempted", False), ("sync_started", True)):
                changed = dict(receipt, **{key: value})
                file.write_text(json.dumps(changed))
                with self.assertRaisesRegex(RuntimeError, "PREVIOUS_READBACK_STOP_REQUIRED"):
                    m.resume_objects(root, previous)

    def test_resume_requires_completed_argo_policy_patch_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, _ = stopped_registration(root)
            file = previous / "receipt.json"
            receipt = json.loads(file.read_text())
            receipt["argo_policy_patch_attempted"] = False
            file.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(RuntimeError, "PREVIOUS_READBACK_STOP_REQUIRED"):
                m.resume_objects(root, previous)

    def test_resume_completes_checks_without_native_bootstrap_apply_or_policy_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, objects = stopped_registration(root)
            kubeconfig = root / "personal-kubeconfig"
            kubeconfig.write_text("PRIVATE_NOT_READ")
            native_log = root / "native.log"
            native_log.write_text(json.dumps({"status": "PASS_CLOUD_RUNTIME_TEMPORARY_PULL_REFERENCE_DETACHED", "kubeconfig_file": str(kubeconfig)}) + "\nPrivate result directory: " + str(root))
            runner = ReviewRunner()
            run_review = runner.run
            def run(*args, **kwargs):
                if args[0] == "whoami":
                    runner.calls += 1
                    return "https://expected" if len(args) > 1 else "tjung03"
                return run_review(*args, **kwargs)
            def get(kind, name, namespace=None):
                runner.calls += 1
                for item in objects:
                    if item["kind"] == kind and item["metadata"]["name"] == name:
                        return copy.deepcopy(item)
                if kind == "argocd":
                    return {"spec": {"rbac": {"scopes": "[groups]", "policy": "\n".join(m.LINES)}}}
                if kind == "configmap":
                    return {"data": {"policy.csv": "\n".join(m.LINES)}}
                raise AssertionError("Unexpected configuration query")
            runner.run, runner.get = run, get
            fcntl = types.SimpleNamespace(flock=lambda *args: None, LOCK_EX=1, LOCK_NB=2)
            args = ["register", str(root), "--native-run-log", str(native_log), "--resume-from", str(previous), "--cloud-api", "https://expected"]
            with patch.object(sys, "argv", args), patch.dict(sys.modules, {"fcntl": fcntl}), \
                 patch.object(m.shutil, "which", return_value=str(kubeconfig)), patch.object(m, "Runner", return_value=runner), \
                 patch.object(m.subprocess, "run", side_effect=AssertionError("No bootstrap subprocess allowed")), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(), 0)
            receipt = json.loads(next(root.glob("cloud-monitoring-registration-*/receipt.json")).read_text())
            self.assertEqual(receipt["status"], "PASS_MONITORING_REGISTERED_MANUAL_SYNC_PENDING")
            self.assertEqual(receipt["configuration_mutations"], 0)
            self.assertFalse(receipt["create_attempted"] or receipt["argo_policy_patch_attempted"] or receipt["native_stage_repeated"])
            self.assertEqual(receipt["controller_checks"], 18)


if __name__ == "__main__":
    unittest.main()
