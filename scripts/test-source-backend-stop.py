"""Exercise only the new read-only stop gate; no cluster, DB or GitHub calls."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("gate", Path(__file__).with_name("verify-source-backend-stop.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SHA = "a" * 40
API = "https://source.invalid:6443"


def fixtures():
    expected = {"applications": {"root": {"uid": "root-uid"}, "apps-backend": {"uid": "child-uid"}},
                "deployment_uid": "backend-uid", "kubeconfig": "/private/config", "context": "source"}
    applications = [{"metadata": {"name": name, "uid": expected["applications"][name]["uid"]},
                     "spec": {"source": {"repoURL": "https://github.com/" + gate.REPO + ".git", "targetRevision": "main",
                                         "path": "apps/backend" if name == "apps-backend" else "argocd/applications"}},
                     "status": {"sync": {"revision": SHA, "status": "Synced"}}} for name in ("root", "apps-backend")]
    snapshot = {"applications": applications, "deployment": {"metadata": {"uid": "backend-uid", "generation": 2},
                "spec": {"replicas": 0, "selector": {"matchLabels": {"app.kubernetes.io/name": "backend"}}},
                "status": {"observedGeneration": 2}}, "service": {"spec": {"selector": {"app.kubernetes.io/name": "backend"}}},
                "pods": [], "slices": [], "endpoints": None, "controllers": []}
    return expected, snapshot


class GateTests(unittest.TestCase):
    def test_unmerged_hold_never_constructs_cluster_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result"
            result.mkdir()
            contract = {"status": "SOURCE_GIT_CONTROL_CONTRACT_COLLECTED", "applications": [{"name": name, "uid": name} for name in ("root", "apps-backend")]}
            source = {"status": "SOURCE_LOCAL_READ_COMPLETED", "contexts": [{"source_contract_match": True, "deployment_uid": "backend", "server_sha256": hashlib.sha256(API.encode()).hexdigest(), "kubeconfig": "/private/config", "context": "source"}]}
            (root / "contract.json").write_text(json.dumps(contract))
            (root / "source.json").write_text(json.dumps(source))
            args = ["gate", "--source-contract", str(root / "contract.json"), "--source-read", str(root / "source.json"), "--source-api", API, "--manifest-sha256", "b" * 64, "--pr", "123"]
            with patch.object(sys, "argv", args), patch.object(gate, "fetch", return_value={"merged": False}), patch.object(gate, "Client", side_effect=AssertionError("Cluster accessed")), patch.object(gate.tempfile, "mkdtemp", return_value=str(result)), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(gate.main(), 0)
            self.assertEqual(json.loads((result / "receipt.json").read_text())["status"], "HOLD_SOURCE_STOP_PR_NOT_MERGED")

    def test_zero_snapshot_and_replaced_uid(self):
        expected, snapshot = fixtures()
        self.assertTrue(gate.evaluate(snapshot, expected, SHA)[0])
        snapshot["deployment"]["metadata"]["uid"] = "other"
        with self.assertRaisesRegex(gate.Stop, "UID_CHANGED"):
            gate.evaluate(snapshot, expected, SHA)

    def test_terminating_pod_blocks(self):
        expected, snapshot = fixtures()
        snapshot["pods"] = [{"metadata": {"deletionTimestamp": "now"}, "status": {"phase": "Succeeded"}}]
        self.assertFalse(gate.evaluate(snapshot, expected, SHA)[0])

    def test_unready_and_legacy_endpoint_addresses_block(self):
        expected, snapshot = fixtures()
        snapshot["slices"] = [{"endpoints": [{"addresses": ["192.0.2.1"], "conditions": {"ready": False}}]}]
        self.assertFalse(gate.evaluate(snapshot, expected, SHA)[0])
        snapshot["slices"] = []
        snapshot["endpoints"] = {"subsets": [{"notReadyAddresses": [{"ip": "192.0.2.1"}]}]}
        self.assertFalse(gate.evaluate(snapshot, expected, SHA)[0])

    def test_active_job_enabled_cron_and_hpa_block(self):
        for controller in ({"kind": "Job", "status": {"active": 1}}, {"kind": "CronJob", "spec": {}}, {"kind": "HorizontalPodAutoscaler", "spec": {"scaleTargetRef": {"name": "backend"}}}):
            expected, snapshot = fixtures()
            snapshot["controllers"] = [controller]
            self.assertFalse(gate.evaluate(snapshot, expected, SHA)[0])

    def test_override_stale_revision_and_running_root_not_accepted(self):
        expected, snapshot = fixtures()
        snapshot["applications"][1]["spec"]["source"]["kustomize"] = {"replicas": [{"name": "backend", "count": 2}]}
        with self.assertRaisesRegex(gate.Stop, "OVERRIDE"):
            gate.evaluate(snapshot, expected, SHA)
        expected, snapshot = fixtures()
        snapshot["applications"][1]["status"]["sync"]["revision"] = "old"
        self.assertFalse(gate.evaluate(snapshot, expected, SHA)[0])
        snapshot["applications"][1]["status"]["sync"]["revision"] = SHA
        snapshot["applications"][0]["status"]["operationState"] = {"phase": "Running"}
        self.assertFalse(gate.evaluate(snapshot, expected, SHA)[0])

    def test_merged_manifest_hash_and_file_scope(self):
        content = b"spec:\n  replicas: 0\n"
        pr = {"merged": True, "base": {"ref": "main", "repo": {"full_name": gate.REPO}}, "merge_commit_sha": SHA}
        files = [{"filename": gate.DEPLOYMENT_PATH, "status": "modified"}]
        with patch.object(gate, "fetch", side_effect=[pr, files, content]):
            self.assertEqual(gate.merged_source(123, hashlib.sha256(content).hexdigest()), SHA)
        with patch.object(gate, "fetch", side_effect=[pr, files, content]):
            with self.assertRaisesRegex(gate.Stop, "HASH_CHANGED"):
                gate.merged_source(123, "b" * 64)
        with patch.object(gate, "fetch", side_effect=[pr, files + [{"filename": "other"}]]):
            with self.assertRaisesRegex(gate.Stop, "SCOPE_CHANGED"):
                gate.merged_source(123, hashlib.sha256(content).hexdigest())

    def test_duplicate_config_paths_same_backend_accepted_other_uid_rejected(self):
        contract = {"status": "SOURCE_GIT_CONTROL_CONTRACT_COLLECTED", "applications": [{"name": name, "uid": name} for name in ("root", "apps-backend")]}
        row = {"source_contract_match": True, "deployment_uid": "backend", "server_sha256": hashlib.sha256(API.encode()).hexdigest(), "kubeconfig": "/private/config", "context": "source"}
        source = {"status": "SOURCE_LOCAL_READ_COMPLETED", "contexts": [row, copy.deepcopy(row)]}
        self.assertEqual(gate.expected_inputs(contract, source, API)["deployment_uid"], "backend")
        source["contexts"][1]["deployment_uid"] = "other"
        with self.assertRaisesRegex(gate.Stop, "NOT_UNIQUE"):
            gate.expected_inputs(contract, source, API)

    def test_reappearing_pod_restarts_the_quiet_interval(self):
        expected, clean = fixtures()
        busy = copy.deepcopy(clean)
        busy["pods"] = [{"metadata": {"name": "returned"}}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result"
            result.mkdir()
            contract = {"status": "SOURCE_GIT_CONTROL_CONTRACT_COLLECTED", "applications": [{"name": name, "uid": name} for name in ("root", "apps-backend")]}
            source = {"status": "SOURCE_LOCAL_READ_COMPLETED", "contexts": [{"source_contract_match": True, "deployment_uid": "backend", "server_sha256": hashlib.sha256(API.encode()).hexdigest(), "kubeconfig": "/private/config", "context": "source"}]}
            (root / "contract.json").write_text(json.dumps(contract))
            (root / "source.json").write_text(json.dumps(source))
            args = ["gate", "--source-contract", str(root / "contract.json"), "--source-read", str(root / "source.json"), "--source-api", API, "--manifest-sha256", "b" * 64, "--pr", "123"]
            with patch.object(sys, "argv", args), patch.object(gate, "merged_source", return_value=SHA), patch.object(gate, "expected_inputs", return_value=expected), patch.object(gate, "Client") as client, patch.object(gate.tempfile, "mkdtemp", return_value=str(result)), patch.object(gate.time, "monotonic", side_effect=[0, 1, 20, 30, 45, 61]), patch.object(gate.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                client.return_value.read.return_value = {"clusters": [{"cluster": {"server": API}}]}
                client.return_value.snapshot.side_effect = [clean, busy, clean, clean, clean]
                self.assertEqual(gate.main(), 0)
                self.assertEqual(client.return_value.snapshot.call_count, 5)
            self.assertEqual(json.loads((result / "receipt.json").read_text())["status"], "PASS_KUBERNETES_BACKEND_STOP_GATE")

    def test_descendant_with_identical_held_manifest_is_accepted_and_cached(self):
        expected, snapshot = fixtures()
        revision = "c" * 40
        snapshot["applications"][1]["status"]["sync"]["revision"] = revision
        content = b"spec:\n  replicas: 0\n"
        digest = hashlib.sha256(content).hexdigest()
        comparison = {"status": "ahead", "ahead_by": 2, "behind_by": 0, "merge_base_commit": {"sha": SHA}}
        cache = {}
        with patch.object(gate, "fetch", side_effect=[comparison, content]) as fetch:
            self.assertTrue(gate.evaluate(snapshot, expected, SHA, digest, cache)[0])
            self.assertTrue(gate.evaluate(snapshot, expected, SHA, digest, cache)[0])
        self.assertEqual(fetch.call_count, 2)
        self.assertIn(SHA + "..." + revision, fetch.call_args_list[0].args[0])

    def test_non_descendant_and_invalid_revision_are_never_accepted(self):
        for comparison in ({"status": "behind"}, {"status": "diverged"},
                           {"status": "ahead", "ahead_by": 1, "behind_by": 0, "merge_base_commit": {"sha": "b" * 40}}):
            with patch.object(gate, "fetch", return_value=comparison) as fetch:
                self.assertFalse(gate.accepted_sync_revision(SHA, "c" * 40, "b" * 64, {}))
                self.assertEqual(fetch.call_count, 1)
        with patch.object(gate, "fetch") as fetch:
            self.assertFalse(gate.accepted_sync_revision(SHA, "main;invalid", "b" * 64, {}))
            fetch.assert_not_called()

    def test_descendant_revert_or_changed_backend_manifest_stops(self):
        comparison = {"status": "ahead", "ahead_by": 1, "behind_by": 0, "merge_base_commit": {"sha": SHA}}
        for content in (b"spec:\n  replicas: 2\n", b"spec:\n  replicas: 0\nimage: changed\n"):
            with patch.object(gate, "fetch", side_effect=[comparison, content]):
                with self.assertRaisesRegex(gate.Stop, "SYNCED_DESCENDANT_MANIFEST_CHANGED"):
                    gate.accepted_sync_revision(SHA, "c" * 40, hashlib.sha256(b"spec:\n  replicas: 0\n").hexdigest(), {})

    def test_comparison_failure_is_not_treated_as_a_stopped_backend(self):
        with patch.object(gate, "fetch", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                gate.accepted_sync_revision(SHA, "c" * 40, "b" * 64, {})


if __name__ == "__main__":
    unittest.main()
