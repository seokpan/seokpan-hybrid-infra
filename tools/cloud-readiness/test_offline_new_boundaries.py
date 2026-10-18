"""New recreated-target and activation-preview boundaries only; no old runtime suite."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import post_import_new_cluster as sql
import prepare_cloud_candidate as candidate


def uid(n):
    return str(n) * 8 + "-1111-1111-1111-" + str(n) * 12


class NewTargetTests(unittest.TestCase):
    def setUp(self):
        self.contract = sql.load_contract()
        self.lifecycle = {"current_cluster_id": "new-cluster", "source_cluster_id": "old-cluster"}
        self.platform = {"cluster_id": "new-cluster", "state": "ready",
                         "api_url": "https://api.new.example.openshiftapps.com:6443"}
        image = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/backend@sha256:" + "a" * 64
        self.held = {"items": [
            {"kind": "Deployment", "metadata": {"name": "backend", "namespace": "seokpan-cloud"},
             "spec": {"replicas": 0, "template": {"spec": {"containers": [{"name": "backend", "image": image}]}}}},
            {"kind": "ConfigMap", "data": {"SEOKPAN_DATABASE_EXPECTED_HOST": "database.example.test",
             "SEOKPAN_DATABASE_EXPECTED_NAME": "stone_game", "SEOKPAN_DATABASE_EXPECTED_PORT": "3306"}}]}
        self.raw = json.dumps(self.held).encode()
        self.target = {"schema": 1, "cluster_id": "new-cluster", "namespace": "seokpan-cloud",
                       "readonly_pod_scope_accepted": True, "namespace_uid": uid(1), "pod_uid": uid(2),
                       "secret_uid": uid(3), "ca_uid": uid(4), "pod_name": "data-admin-new", "container": "db-admin",
                       "oc": "/trusted/oc", "personal_kubeconfig": "/private/new-kubeconfig",
                       "held_bundle_sha256": hashlib.sha256(self.raw).hexdigest()}
        self.handoff = {"accepted_by": "tjung03", "reported_schema_revision": "20260902_0002",
                        "final_import_compare_and_schema_handoff_accepted": True, "source_writer_hold_accepted": True,
                        "final_import_evidence_ref": "run-final-import", "source_writer_hold_evidence_ref": "run-stop"}
        self.probe = (Path(__file__).parent / "accepted_sql_probe.py").read_bytes()
        self.pod = {"metadata": {"uid": uid(2)}, "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
                    "spec": {"containers": [{"name": "db-admin", "image": image,
                                              "volumeMounts": [{"name": "ca", "mountPath": "/run/rds-ca", "readOnly": True}]}],
                             "volumes": [{"name": "ca", "configMap": {"name": "backend-database-ca"}}]}}
        self.outputs = [
            {"clusters": [{"cluster": {"server": self.platform["api_url"]}}]}, b"tjung03\n",
            {"metadata": {"uid": uid(1)}}, self.pod,
            {"metadata": {"uid": uid(3)}, "data": {key: base64.b64encode(
                ("mysql+asyncmy://" + user + ":synthetic-password@database.example.test:3306/stone_game").encode()).decode()
                for key, user in (("SEOKPAN_IDENTITY_DATABASE_URL", "identity_svc"), ("SEOKPAN_GAME_DATABASE_URL", "game_svc"))}},
            {"metadata": {"uid": uid(4)}, "data": {"ca.crt": "synthetic-ca"}},
            {"status": "PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION", "database_writes": 0, "application_started": False}]
        self.runner = Mock(side_effect=[Mock(returncode=0, stdout=x if isinstance(x, bytes) else json.dumps(x).encode()) for x in self.outputs])

    def execute(self):
        return sql.execute(self.lifecycle, self.platform, self.target, self.handoff, self.raw,
                           self.contract, self.probe, self.runner)

    def test_missing_final_import_stops_before_any_runner(self):
        self.handoff["final_import_compare_and_schema_handoff_accepted"] = False
        with self.assertRaises(self.contract.Stop): self.execute()
        self.runner.assert_not_called()

    def test_old_cluster_rejected_before_any_runner(self):
        self.lifecycle["current_cluster_id"] = "old-cluster"
        with self.assertRaises(sql.Stop): self.execute()
        self.runner.assert_not_called()

    def test_old_data_pod_uid_rejected_before_any_runner(self):
        self.target["pod_uid"] = "97b7b533-b72c-43dd-8afd-0100bcb144aa"
        with self.assertRaises(sql.Stop): self.execute()
        self.runner.assert_not_called()

    def test_changed_held_bundle_rejected_before_api(self):
        self.target["held_bundle_sha256"] = "0" * 64
        with self.assertRaises(sql.Stop): self.execute()
        self.runner.assert_not_called()

    def test_namespace_uid_change_stops_before_secret_or_exec(self):
        self.outputs[2]["metadata"]["uid"] = uid(9)
        self.runner.side_effect = [Mock(returncode=0, stdout=x if isinstance(x, bytes) else json.dumps(x).encode()) for x in self.outputs]
        with self.assertRaises(sql.Stop): self.execute()
        self.assertEqual(self.runner.call_count, 3)

    def test_ca_mount_mismatch_stops_before_secrets_or_exec(self):
        self.pod["spec"]["containers"][0]["volumeMounts"][0]["mountPath"] = "/wrong"
        self.runner.side_effect = [Mock(returncode=0, stdout=x if isinstance(x, bytes) else json.dumps(x).encode()) for x in self.outputs]
        with self.assertRaises(sql.Stop): self.execute()
        self.assertEqual(self.runner.call_count, 4)

    def test_probe_tamper_rejected_before_api(self):
        self.probe += b"\n# changed"
        with self.assertRaises(sql.Stop): self.execute()
        self.runner.assert_not_called()

    def test_new_target_flow_only_read_and_probe(self):
        result = self.execute()
        self.assertTrue(result["new_cluster_bound"])
        self.assertFalse(result["old_token_or_pod_reused"])
        self.assertEqual(self.runner.call_count, 7)
        self.assertEqual([call.args[0][3] for call in self.runner.call_args_list],
                         ["config", "whoami", "get", "get", "get", "get", "exec"])
        last = self.runner.call_args
        self.assertIn("data-admin-new", last.args[0])
        self.assertNotIn("data-admin-327da1ec88", last.args[0])
        self.assertIn("synthetic-password", last.kwargs["input"].decode())
        self.assertNotIn("synthetic-password", json.dumps(result))


class NewCandidateTests(unittest.TestCase):
    def test_staged_replica_contract(self):
        self.assertEqual(candidate.STAGES, {"single": (1, 1), "shared": (2, 1), "target": (3, 3)})

    def test_timeout_five_is_proposal_and_inputs_not_changed(self):
        original = {"route_host": "game.example.test", "database_host": "db.example.test", "redis_host": "redis.example.test"}
        before = copy.deepcopy(original)
        _, env, missing = candidate.stage_overlay("single", original)
        self.assertEqual(original, before)
        self.assertEqual(missing, [])
        self.assertEqual(env["SEOKPAN_DATABASE_POOL_TIMEOUT_SECONDS"], "5")
        self.assertEqual(env["SEOKPAN_DATABASE_POOL_SIZE"], "2")

    def test_full_origin_or_credentials_as_host_rejected(self):
        for value in ("https://game.example.test", "user:password@db.example.test", "db.example.test\nBAD=x"):
            with self.assertRaises(ValueError): candidate.configuration({"database_host": value})

    def test_secret_input_rejected(self):
        with self.assertRaises(ValueError): candidate.configuration({"password": "never-allowed"})

    def test_single_has_no_target_pdb(self):
        overlay, _, _ = candidate.stage_overlay("single", {})
        self.assertNotIn("pdb.json", overlay["resources"])
        target, _, _ = candidate.stage_overlay("target", {})
        self.assertIn("pdb.json", target["resources"])

    def test_three_hosts_remain_unresolved_until_new_inputs(self):
        _, _, _, pending = candidate.configuration({})
        self.assertEqual(pending, ["route_host", "database_host", "redis_host"])


if __name__ == "__main__":
    unittest.main()
