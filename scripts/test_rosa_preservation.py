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
            raw=json.dumps({"sops":{"age":[{"recipient":"age1fixture"}]}}).encode()
            cipher=d/"cipher.json";cipher.write_bytes(raw);cipher.chmod(0o600)
            c.update(recipient_source=str(cipher),recipient_source_sha256=hashlib.sha256(raw).hexdigest())
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

class RecipientFixBoundaries(unittest.TestCase):
    def test_quoted_json_metadata_without_payload_recipient_confusion(self):
        raw=json.dumps({"recipient":"age1ignoredpayload","sops":{"age":[{"recipient":"age1fixture"}]}}).encode()
        self.assertEqual(B.age_recipient(raw),("age1fixture","SOPS_JSON"))
    def test_block_yaml_quoted_and_indentless_lists(self):
        for text in [
            'payload: ENC[fixture]\nsops:\n    age:\n        - recipient: age1fixture\n          enc: fixture\n    version: 3.13.3\n',
            'payload: ENC[fixture]\n"sops":\n  "age":\n  - enc: fixture\n    "recipient": "age1fixture"\n  version: 3.13.3\n']:
            with self.subTest(text=text):
                self.assertEqual(B.age_recipient(text.encode()),("age1fixture","SOPS_BLOCK_YAML"))
    def test_missing_multiple_and_duplicate_metadata_rejected(self):
        for raw in [b'{"recipient":"age1payload","sops":{"age":[]}}',
                    b'{"sops":{"age":[{"recipient":"age1first"},{"recipient":"age1second"}]}}',
                    b'{"sops":{"age":[{"recipient":"age1first","recipient":"age1second"}]}}']:
            with self.subTest(raw=raw),self.assertRaises(RuntimeError) as caught:
                B.age_recipient(raw)
            self.assertNotIn("age1",str(caught.exception))
    def test_bad_recipient_stops_before_any_cluster_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);raw=b'{"sops":{"age":[]}}'
            cipher=d/"cipher.json";cipher.write_bytes(raw);cipher.chmod(0o600)
            c={"schema":1,"expected_api":"https://test.invalid","recipient_source":str(cipher),"recipient_source_sha256":hashlib.sha256(raw).hexdigest()}
            contract=d/"contract.json";contract.write_text(json.dumps(c));contract.chmod(0o600)
            with patch.object(B.subprocess,"run") as mocked:
                with self.assertRaisesRegex(RuntimeError,"ONE_EXISTING_AGE_RECIPIENT"):
                    B.backup(argparse.Namespace(contract=str(contract)),d,"unused")
                mocked.assert_not_called()
    def test_resume_only_known_stop_and_no_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);p=d/"receipt.json"
            p.write_text(json.dumps({"status":"STOP_CHECKPOINT","reason":"ONE_EXISTING_AGE_RECIPIENT_REQUIRED","resource_mutations":0,"sync_started":False}));p.chmod(0o600)
            self.assertEqual(B.validate_resume(d),str(d))
            (d/"checkpoint.sops.json").write_text("uncertain-existing-cipher")
            with self.assertRaisesRegex(RuntimeError,"RESUME_REQUIRED"):B.validate_resume(d)
    def test_unrelated_stop_not_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);p=d/"receipt.json"
            p.write_text(json.dumps({"status":"STOP_CHECKPOINT","reason":"CHECKPOINT_DECRYPT_FAILED","resource_mutations":0}));p.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError,"RESUME_REQUIRED"):B.validate_resume(d)
    def test_auth_stop_does_not_repeat_reads_or_encrypt(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);raw=b'{"sops":{"age":[{"recipient":"age1fixture"}]}}'
            c={"schema":1,"expected_api":"https://test.invalid","cloud_namespace":"cloud","argo_namespace":"argo", "previous_inventory":str(d),"inventory_counts":{"pvc":4,"cloud_secrets":8},"recipient_source":str(d/"cipher.json"),"recipient_source_sha256":hashlib.sha256(raw).hexdigest()}
            for name,data in [("cipher.json",raw),("contract.json",json.dumps(c).encode()),("kubeconfig",b"fixture"),("receipt.json",b'{"pvc_count":4,"secret_inventory_counts":{"cloud":8}}')]:
                (d/name).write_bytes(data);(d/name).chmod(0o600)
            cfg=b'{"clusters":[{"cluster":{"server":"https://test.invalid"}}]}'
            responses=[subprocess.CompletedProcess([],0,cfg,b""),subprocess.CompletedProcess([],1,b"",b"Unauthorized")]
            with patch.object(B.subprocess,"run",side_effect=responses) as called,patch.object(B,"crypt") as encrypted:
                with self.assertRaisesRegex(RuntimeError,"AUTH_REQUIRED_NO_RETRY"):
                    B.backup(argparse.Namespace(contract=str(d/"contract.json"),kubeconfig=str(d/"kubeconfig")),d,"unused")
                self.assertEqual(called.call_count,2)
                encrypted.assert_not_called()

class RecipientDiagnosticBoundaries(unittest.TestCase):
    def test_multiple_json_recipients_report_counts_not_values(self):
        raw=b'{"sops":{"age":[{"recipient":"age1firstprivate"},{"recipient":"age1secondprivate"}]}}'
        result=B.recipient_shape(raw)
        self.assertEqual(result["unique_age_syntax_scalars"],2)
        self.assertFalse(result["current_single_recipient_parser_accepts"])
        self.assertNotIn("age1",json.dumps(result))
    def test_missing_and_invalid_are_distinguishable(self):
        missing=B.recipient_shape(b'{"sops":{"age":[]}}')
        invalid=B.recipient_shape(b'{"sops":{"age":[{"recipient":"NON_AGE_PRIVATE_VALUE"}]}}')
        self.assertEqual(missing["recipient_fields"],0)
        self.assertEqual(invalid["recipient_fields"],1)
        self.assertEqual(invalid["age_syntax_scalars"],0)
        self.assertNotIn("PRIVATE",json.dumps(invalid))
    def test_yaml_scalar_styles_are_reported_without_content(self):
        raw=b'payload: ENC[PRIVATE_PAYLOAD]\nsops:\n  age:\n  - recipient: >-\n      age1privatefoldedvalue\n    enc: PRIVATE_ENVELOPE\n'
        result=B.recipient_shape(raw)
        self.assertEqual(result["recipient_fields"],1)
        self.assertEqual(result["scalar_styles"],{"block":1})
        self.assertFalse(result["current_single_recipient_parser_accepts"])
        self.assertNotIn("PRIVATE",json.dumps(result))
        self.assertNotIn("age1",json.dumps(result))
    def test_duplicate_metadata_is_diagnostic_not_acceptance(self):
        result=B.recipient_shape(b'{"sops":{"age":[],"age":[{"recipient":"age1fixture"}]}}')
        self.assertEqual(result["duplicate_json_keys"],1)
        self.assertFalse(result["current_single_recipient_parser_accepts"])
    def test_diagnose_cli_needs_no_key_binary_or_cluster_calls(self):
        import contextlib,io,sys
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);raw=b'{"sops":{"age":[{"recipient":"age1fixture"}]}}'
            c={"schema":1,"recipient_source":str(d/"cipher.json"),"recipient_source_sha256":hashlib.sha256(raw).hexdigest()}
            for n,data in [("cipher.json",raw),("contract.json",json.dumps(c).encode())]:
                (d/n).write_bytes(data);(d/n).chmod(0o600)
            captured=io.StringIO()
            with patch.object(sys,"argv",["checkpoint.py","diagnose","--contract",str(d/"contract.json"),"--output-parent",str(d)]),patch.object(B.subprocess,"run") as called,patch.object(B,"sops_binary") as binary,contextlib.redirect_stdout(captured):
                self.assertEqual(B.main(),0)
                called.assert_not_called();binary.assert_not_called()
            result=json.JSONDecoder().raw_decode(captured.getvalue())[0]
            self.assertFalse(result["backup_retried"])
            self.assertFalse(result["encryption_attempted"])
            self.assertFalse(result["archive_written"])
            self.assertNotIn("age1fixture",captured.getvalue())

class PreservationInputInspectionBoundaries(unittest.TestCase):
    def fixture(self,d,count=1):
        c={"schema":1,"expected_api":"https://test.invalid","previous_inventory":str(d),"inventory_counts":{"pvc":count},"local_roots":[str(d)]}
        rows=[{"namespace":"cloud","name":"claim"+str(i),"uid":"uid"+str(i),"volume":"pv"+str(i)} for i in range(count)]
        backend=d/"backend.json";variables=d/"vars.json"
        values={"contract.json":c,"receipt.json":{"pvc_count":count},"pvc-inventory.json":rows,
                "backend.json":{"backend":{"type":"s3","config":{"key":"phase2/rosa/terraform.tfstate","secret":"PRIVATE_FIXTURE"}}},
                "vars.json":{"foundation":{"credential":"PRIVATE_FIXTURE"},"cluster_name":"PRIVATE_CLUSTER_NAME"},
                "local-preservation-file-inventory.json":[{"path":str(backend)},{"path":str(variables)}]}
        for n,v in values.items():(d/n).write_text(json.dumps(v));(d/n).chmod(0o600)
        (d/"kubeconfig").write_text("fixture");(d/"kubeconfig").chmod(0o600)
        source=d/"terraform/rosa";source.mkdir(parents=True)
        (source/"main.tf").write_text("fixture");(source/".terraform.lock.hcl").write_text("fixture")
        return argparse.Namespace(contract=str(d/"contract.json"),kubeconfig=str(d/"kubeconfig"))
    def config(self,server="https://test.invalid"):
        import subprocess
        return subprocess.CompletedProcess([],0,json.dumps({"clusters":[{"cluster":{"server":server}}]}).encode(),b"")
    def test_target_guard_blocks_all_remote_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);a=self.fixture(d)
            with patch.object(B.subprocess,"run",return_value=self.config("https://wrong.invalid")) as called:
                with self.assertRaisesRegex(RuntimeError,"TARGET_MISMATCH"):B.inspect_inputs(a)
                self.assertEqual(called.call_count,1)
                self.assertIn("config",called.call_args.args[0])
    def test_catalog_and_pv_details_omit_protected_values(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);a=self.fixture(d)
            pv={"spec":{"claimRef":{"uid":"uid0"},"persistentVolumeReclaimPolicy":"Retain","csi":{"driver":"test.csi","volumeHandle":"PRIVATE_VOLUME_HANDLE"}}}
            with patch.object(B.subprocess,"run",side_effect=[self.config(),subprocess.CompletedProcess([],0,json.dumps(pv).encode(),b"")]):
                r=B.inspect_inputs(a)
            self.assertTrue(r["pvcs"][0]["claim_uid_matches"])
            self.assertEqual(len(r["tf_roots"]),1)
            self.assertFalse(r["plan_started"])
            self.assertNotIn("PRIVATE",json.dumps(r))
    def test_forbidden_pv_does_not_skip_local_plan_catalog(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);a=self.fixture(d)
            with patch.object(B.subprocess,"run",side_effect=[self.config(),subprocess.CompletedProcess([],1,b"",b"Forbidden")]) as called:
                r=B.inspect_inputs(a)
            self.assertEqual(called.call_count,2)
            self.assertEqual(len(r["plan_inputs"]),2)
            self.assertTrue(r["status"].startswith("PARTIAL_"))
    def test_auth_failure_skips_remaining_pvs_but_keeps_local_catalog(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);a=self.fixture(d,count=2)
            with patch.object(B.subprocess,"run",side_effect=[self.config(),subprocess.CompletedProcess([],1,b"",b"Unauthorized")]) as called:
                r=B.inspect_inputs(a)
            self.assertEqual(called.call_count,2)
            self.assertEqual(r["pvcs"][1]["read"],"SKIPPED_AFTER_AUTH_REQUIRED")
            self.assertEqual(len(r["plan_inputs"]),2)

if __name__=="__main__":unittest.main()
