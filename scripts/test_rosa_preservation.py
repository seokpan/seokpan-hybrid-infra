"""New preservation/lifecycle boundaries only; no real credentials or cloud calls."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+".py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
B=load("prepare-rosa-checkpoint")
L=load("rosa-lifecycle-plan")

def item(kind="rhcs_cluster_rosa_classic",name="cluster",actions=None,identifier="TEST_CLUSTER"):
    return {"mode":"managed","type":kind,"name":name,"change":{"actions":actions or ["delete"],"before":{"id":identifier}}}

class PreservationBoundaries(unittest.TestCase):
    def test_delete_requires_exact_cluster_and_scope(self):
        self.assertEqual(L.scope({"resource_changes":[item()]},"delete","TEST_CLUSTER"),{"rhcs_cluster_rosa_classic":1})
        with self.assertRaisesRegex(RuntimeError,"CLUSTER_ID_MISMATCH"):
            L.scope({"resource_changes":[item()]},"delete","ANOTHER_CLUSTER")
    def test_foundation_and_replacement_are_rejected(self):
        for other in [item("aws_db_instance","data"),item(actions=["delete","create"]),dict(item(),module_address="module.foreign")]:
            with self.assertRaises(RuntimeError):L.scope({"resource_changes":[item(),other]},"delete","TEST_CLUSTER")
    def test_apply_without_approval_has_no_subprocess_calls(self):
        a=argparse.Namespace(approval_file=None,manifest=None)
        with patch.object(L.subprocess,"run") as mocked:
            with self.assertRaisesRegex(RuntimeError,"APPROVAL_FILE"):L.apply(a,Path("unused"))
            mocked.assert_not_called()
    def test_false_preservation_gate_blocks_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            m={"action":"delete","cluster_id":"TEST_CLUSTER","plan_sha256":"a"*64}
            approved={**m,"approved_by_user":True,"approval_reference":"test-reference"}
            for name,obj in [("m.json",m),("a.json",approved)]:
                (d/name).write_text(json.dumps(obj));(d/name).chmod(0o600)
            a=argparse.Namespace(approval_file=str(d/"a.json"),manifest=str(d/"m.json"))
            with patch.object(L.subprocess,"run") as mocked:
                with self.assertRaisesRegex(RuntimeError,"PRESERVATION_AND_OWNER"):L.apply(a,d)
                mocked.assert_not_called()
    def test_backend_mismatch_blocks_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);p=data/"terraform.tfstate"
            p.write_text(json.dumps({"backend":{"type":"s3","config":{"key":"phase2/foundation/terraform.tfstate"}}}));p.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError,"BACKEND_KEY"):L.environment(Path("source/terraform/rosa"),data)
    def test_age_identity_excluded_and_no_file_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"settings.json").write_text('{"safe":"test"}')
            (root/"identity-to-deliver.txt").write_text("PRIVATE_KEY_TEST")
            (root/"odd-name.txt").write_text("AGE-SECRET-KEY-1"+"A"*58)
            rows=B.collect_files([root],set())
            self.assertEqual([x["path"] for x in rows],["settings.json"])
    def test_archive_hash_mismatch_stops_before_decrypt(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);p=d/"cipher.json";p.write_bytes(b"encrypted-test-only");p.chmod(0o600)
            a=argparse.Namespace(archive=str(p),archive_sha256="0"*64,key_file="not-read")
            with patch.object(B,"crypt") as mocked:
                with self.assertRaisesRegex(RuntimeError,"ARCHIVE_HASH_MISMATCH"):B.verify(a,d,"unused")
                mocked.assert_not_called()
    def test_auto_inputs_rejected_not_silently_added_to_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            for name in ("cluster.tf",".terraform.lock.hcl","foreign.auto.tfvars.json"):(d/name).write_text("test")
            with self.assertRaisesRegex(RuntimeError,"UNREVIEWED_AUTO_INPUTS"):L.source_hash(d)
    def test_json_terraform_source_is_bound_to_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            (d/"cluster.tf.json").write_text('{"terraform":{}}')
            (d/".terraform.lock.hcl").write_text("test-lock")
            before=L.source_hash(d)
            (d/"cluster.tf.json").write_text('{"terraform":{"required_version":"1.16.4"}}')
            self.assertNotEqual(before,L.source_hash(d))
    def test_stale_inventory_stops_before_cluster_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            c={"schema":1,"expected_api":"https://test.invalid","cloud_namespace":"test-cloud","previous_inventory":str(d),"inventory_counts":{"pvc":4,"cloud_secrets":8}}
            for name,obj in [("contract.json",c),("receipt.json",{"pvc_count":99})]:
                (d/name).write_text(json.dumps(obj));(d/name).chmod(0o600)
            a=argparse.Namespace(contract=str(d/"contract.json"))
            with patch.object(B.subprocess,"run") as mocked:
                with self.assertRaisesRegex(RuntimeError,"ACCEPTED_PREVIOUS_INVENTORY"):B.backup(a,d,"unused")
                mocked.assert_not_called()
    def test_encryption_failure_does_not_echo_payload_or_raw_stderr(self):
        import subprocess
        with patch.object(B.os,"name","posix"),patch.object(B.subprocess,"run",return_value=subprocess.CompletedProcess([],1,b"",b"PRIVATE_TEST_VALUE")):
            with self.assertRaises(RuntimeError) as caught:B.crypt("unused",b"PRIVATE_TEST_VALUE",recipient="age1test")
            self.assertEqual(str(caught.exception),"CHECKPOINT_ENCRYPT_FAILED")
            self.assertNotIn("PRIVATE_TEST_VALUE",str(caught.exception))

if __name__=="__main__":unittest.main()
