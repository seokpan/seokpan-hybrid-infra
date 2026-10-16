"""Offline scope and failure checks for manual monitoring bootstrap."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
