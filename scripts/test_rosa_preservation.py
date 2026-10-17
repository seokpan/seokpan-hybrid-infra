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
            with patch.object(B.subprocess,"run",side_effect=responses) as called,patch.object(B,"crypt") as encrypted,patch.object(B,"key_matched_recipient",return_value="age1fixture"):
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

class ExistingKeyRecipientSelectionBoundaries(unittest.TestCase):
    def identity(self,d):
        p=d/"identity.txt";p.write_bytes(b"# fixture only\nAGE-SECRET-KEY-1FIXTUREONLY\n");p.chmod(0o600)
        return argparse.Namespace(key_file=str(p),age_keygen="fixture-keygen")
    def test_existing_key_selects_one_of_three_without_broadening_access(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);a=self.identity(d)
            with patch.object(B,"age_keygen_binary",return_value="fixture-keygen"),patch.object(B.subprocess,"run",return_value=subprocess.CompletedProcess([],0,b"age1second\n",b"")) as called:
                self.assertEqual(B.key_matched_recipient(["age1first","age1second","age1third"],a,d),"age1second")
            self.assertEqual(called.call_args.args[0],["fixture-keygen","-y"])
            self.assertNotIn("AGE-SECRET",str(called.call_args.args[0]))
    def test_wrong_existing_key_stops_without_choosing_first(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);a=self.identity(d)
            with patch.object(B,"age_keygen_binary",return_value="fixture-keygen"),patch.object(B.subprocess,"run",return_value=subprocess.CompletedProcess([],0,b"age1other\n",b"")):
                with self.assertRaisesRegex(RuntimeError,"EXISTING_KEY_MATCHING_SOURCE"):B.key_matched_recipient(["age1first","age1second","age1third"],a,d)
    def test_multi_identity_file_is_not_exported_or_implicitly_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);a=self.identity(d)
            Path(a.key_file).write_bytes(b"AGE-SECRET-KEY-1FIRST\nAGE-SECRET-KEY-1SECOND\n")
            with patch.object(B,"age_keygen_binary") as binary:
                with self.assertRaisesRegex(RuntimeError,"ONE_EXISTING_X25519_IDENTITY"):B.key_matched_recipient(["age1first"],a,d)
                binary.assert_not_called()
    def test_derivation_failure_never_echoes_raw_output(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);a=self.identity(d)
            with patch.object(B,"age_keygen_binary",return_value="fixture-keygen"),patch.object(B.subprocess,"run",return_value=subprocess.CompletedProcess([],1,b"PRIVATE",b"PRIVATE")):
                with self.assertRaises(RuntimeError) as caught:B.key_matched_recipient(["age1first"],a,d)
                self.assertEqual(str(caught.exception),"EXISTING_IDENTITY_PUBLIC_DERIVATION_FAILED")
    def test_multi_recipient_backup_reaches_target_guard_after_key_match(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            raw=json.dumps({"sops":{"age":[{"recipient":v} for v in ["age1first","age1second","age1third"]]}}).encode()
            c={"schema":1,"expected_api":"https://expected.invalid","cloud_namespace":"cloud","previous_inventory":str(d),"inventory_counts":{"pvc":0,"cloud_secrets":0},"recipient_source":str(d/"cipher.json"),"recipient_source_sha256":hashlib.sha256(raw).hexdigest()}
            for n,data in [("cipher.json",raw),("contract.json",json.dumps(c).encode()),("receipt.json",b'{"pvc_count":0,"secret_inventory_counts":{"cloud":0}}'),("kubeconfig",b"fixture")]:
                (d/n).write_bytes(data);(d/n).chmod(0o600)
            cfg=b'{"clusters":[{"cluster":{"server":"https://wrong.invalid"}}]}'
            with patch.object(B,"key_matched_recipient",return_value="age1second") as matched,patch.object(B.subprocess,"run",return_value=subprocess.CompletedProcess([],0,cfg,b"")):
                with self.assertRaisesRegex(RuntimeError,"TARGET_MISMATCH"):B.backup(argparse.Namespace(contract=str(d/"contract.json"),kubeconfig=str(d/"kubeconfig")),d,"unused")
                self.assertEqual(len(matched.call_args.args[0]),3)

class PlanSupplementBoundaries(unittest.TestCase):
    def test_whitelisted_plan_files_are_encrypted_without_identity_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);out=d/"out";out.mkdir();plan=d/"plan";plan.mkdir()
            raw=b'{"sops":{"age":[{"recipient":"age1fixture"}]}}'
            c={"schema":1,"recipient_source":str(d/"cipher.json"),"recipient_source_sha256":hashlib.sha256(raw).hexdigest()}
            for path,data in [(d/"cipher.json",raw),(d/"contract.json",json.dumps(c).encode()),(plan/"receipt.json",b'{}'),(plan/"before.tfstate",b'PRIVATE_STATE_FIXTURE'),(plan/"identity.txt",b'PRIVATE_IDENTITY_FIXTURE')]:
                path.write_bytes(data);path.chmod(0o600)
            captured={}
            def crypto(binary,data,decrypt=False,**kwargs):
                if decrypt:return captured["raw"]
                captured["raw"]=data;return b"encrypted-fixture-only"
            a=argparse.Namespace(contract=str(d/"contract.json"),seal_dir=str(plan),key_file="fixture")
            with patch.object(B,"key_matched_recipient",return_value="age1fixture"),patch.object(B,"crypt",side_effect=crypto):
                r=B.seal(a,out,"fixture",{})
            self.assertTrue(r["local_decrypt_verified"])
            self.assertEqual({x["path"] for x in json.loads(captured["raw"])["files"]},{"receipt.json","before.tfstate"})
            self.assertNotIn(b"PRIVATE",(out/"checkpoint.sops.json").read_bytes())
    def test_missing_plan_receipt_stops_before_encryption(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);raw=b'{"sops":{"age":[{"recipient":"age1fixture"}]}}'
            c={"schema":1,"recipient_source":str(d/"cipher.json"),"recipient_source_sha256":hashlib.sha256(raw).hexdigest()}
            for n,data in [("cipher.json",raw),("contract.json",json.dumps(c).encode())]:(d/n).write_bytes(data);(d/n).chmod(0o600)
            a=argparse.Namespace(contract=str(d/"contract.json"),seal_dir=str(d),key_file="fixture")
            with patch.object(B,"key_matched_recipient",return_value="age1fixture"),patch.object(B,"crypt") as crypt:
                with self.assertRaisesRegex(RuntimeError,"PLAN_RECEIPT_REQUIRED"):B.seal(a,d,"fixture",{})
                crypt.assert_not_called()

class AcceptedArchivePlanResumeBoundaries(unittest.TestCase):
    def fixture(self,d):
        old=d/'batch';old.mkdir();out=d/'out';out.mkdir()
        archive=d/'checkpoint.sops.json';archive.write_bytes(b'encrypted-fixture-only');archive.chmod(0o600)
        key=d/'key.txt';key.write_bytes(b'not-a-real-key-fixture');key.chmod(0o600)
        row={'archive':str(archive),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
             'local_decrypt_verified':True,'archive_written':True}
        h={'schema':1,'deletion_ready':False,'plan_exit_code':1,'plan_phase':'EXISTING_ROSA_TOKEN_REFRESH',
           'archives':[{'path':str(archive),'sha256':row['archive_sha256'],'kind':'config-backup'}],
           'key_source':str(key),'key_sha256':hashlib.sha256(key.read_bytes()).hexdigest()}
        for name,v in [('config-backup.log',row),('handoff.json',h)]:
            p=old/name;p.write_text(json.dumps(v));p.chmod(0o600)
        return old,key,out,archive,h
    def invoke(self,old,key,out):
        import subprocess,sys
        source=(HERE/'rosa-preservation-batch.sh').read_text()
        fragment=source.split('# RESUME_ARCHIVE_METADATA_ONLY:',1)[1].split("<<'PY'\n",1)[1].split('\nPY\n',1)[0]
        return subprocess.run([sys.executable,'-I','-c',fragment,str(old),str(key),str(out)],capture_output=True,text=True)
    def test_accepted_archive_is_reused_without_encryption_or_decrypt(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);old,key,out,archive,h=self.fixture(d);before=archive.read_bytes()
            p=self.invoke(old,key,out)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(archive.read_bytes(),before)
            self.assertEqual(json.loads((out/'config-backup.log').read_text())['archive_sha256'],h['archives'][0]['sha256'])
            self.assertNotIn('not-a-real-key',p.stdout+p.stderr)
    def test_corrupt_archive_blocks_resume_before_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            old,key,out,archive,h=self.fixture(Path(tmp));archive.write_bytes(b'changed')
            p=self.invoke(old,key,out)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('STOP_EXISTING_ARCHIVE_HASH',p.stderr)
            self.assertFalse((out/'config-backup.log').exists())
    def test_existing_plan_requires_reconciliation_instead_of_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            old,key,out,archive,h=self.fixture(Path(tmp));(old/'rosa-lifecycle-existing').mkdir()
            p=self.invoke(old,key,out)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('STOP_PLAN_ALREADY_ATTEMPTED_RECONCILE',p.stderr)
    def test_changed_operator_key_is_not_implicitly_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            old,key,out,archive,h=self.fixture(Path(tmp));key.write_bytes(b'another-fixture')
            p=self.invoke(old,key,out)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('STOP_EXISTING_KEY_BINDING',p.stderr)

class CloneableLifecycleEntryBoundaries(unittest.TestCase):
    def fixture(self, d, action='delete'):
        E=load('rosa-lifecycle-entry')
        helper=d/'auth.sh';helper.write_bytes(b'fixture-not-executed\n')
        E.AUTH_HELPER_BLOB=hashlib.sha1(b'blob '+str(helper.stat().st_size).encode()+b'\0'+helper.read_bytes()).hexdigest()
        cli=d/'rosa';cli.write_bytes(b'fixture-not-executed\n');cli.chmod(0o700)
        (d/'terraform/rosa').mkdir(parents=True)
        approval={'action':'delete','cluster_id':'TEST_CLUSTER','plan_sha256':'f'*64,'approval_reference':'fixture-only','approved_by_user':False}
        manifest={'action':'delete','cluster_id':'TEST_CLUSTER','plan_sha256':'f'*64}
        variables={'cluster_enabled':True,'cluster_name':'fixture','worker_sg_binding':None}
        for name,value in [('approval.json',approval),('manifest.json',manifest),('vars.json',variables)]:
            p=d/name;p.write_text(json.dumps(value));p.chmod(0o600)
        c={'schema':1,'scope':'ROSA_ONLY','current_cluster_id':'TEST_CLUSTER',
           'auth':{'rosa_cli':str(cli),'tf_session_helper':str(helper)},
           'delete':{'manifest':str(d/'manifest.json'),'approval_file':str(d/'approval.json')},
           'create':{'var_file':str(d/'vars.json'),'cluster_name':'fixture','inputs_accepted':True,'network_ready':True,'budget_accepted':True}}
        p=d/'config.json';p.write_text(json.dumps(c));p.chmod(0o600)
        return E,p,c
    def test_delete_without_user_approval_stops_before_cloud_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);E,p,c=self.fixture(d)
            with patch.object(E.subprocess,'run') as calls:
                with self.assertRaisesRegex(RuntimeError,'EXPLICIT_PLAN_BOUND_USER_DELETE_APPROVAL'):E.settings(p,'delete',d)
                calls.assert_not_called()
    def test_scope_cannot_expand_to_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);E,p,c=self.fixture(d);c['scope']='FOUNDATION_AND_NETWORK';p.write_text(json.dumps(c))
            with self.assertRaisesRegex(RuntimeError,'ROSA_ONLY_SCOPE'):E.settings(p,'delete',d)
    def test_create_rejects_old_worker_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);E,p,c=self.fixture(d,'create');v=json.loads((d/'vars.json').read_text());v['worker_sg_binding']={'fixture':'old'};(d/'vars.json').write_text(json.dumps(v))
            with self.assertRaisesRegex(RuntimeError,'NEW_CLUSTER_UNBOUND'):E.settings(p,'create',d)
    def test_missing_restore_driver_stops_before_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);E,p,c=self.fixture(d,'create')
            with patch.object(E.subprocess,'run') as calls:
                with self.assertRaisesRegex(RuntimeError,'REGULAR_PINNED_TOOL'):E.settings(p,'create',d)
                calls.assert_not_called()
    def test_reviewed_driver_and_network_acceptance_prepare_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);E,p,c=self.fixture(d,'create');driver=d/'restore.sh';driver.write_text('fixture-not-executed\n')
            c['create'].update(restore_driver=str(driver),restore_driver_sha256=E.L.sha(driver),held_restoration_contract_accepted=True);p.write_text(json.dumps(c))
            value,paths=E.settings(p,'create',d)
            self.assertEqual(value['scope'],'ROSA_ONLY');self.assertEqual(paths['rosa_cli'],c['auth']['rosa_cli'])
    def test_creation_and_deletion_have_distinct_required_conditions(self):
        self.assertEqual(set(L.approval_gates('create')),{'recreation_inputs_accepted','network_ready','budget_accepted'})
        self.assertIn('independent_decrypt_access_accepted',L.approval_gates('delete'))

class RestorationGapBoundaries(unittest.TestCase):
    def fixture(self):
        G=load('collect-rosa-restoration-gaps')
        claim={'namespace':'openshift-monitoring','name':'fixture','uid':'claim-one','volume':'pv-one'}
        pv={'apiVersion':'v1','kind':'PersistentVolume','metadata':{'name':'pv-one','uid':'pv-uid'},
            'spec':{'claimRef':{'namespace':claim['namespace'],'name':claim['name'],'uid':claim['uid']},
                    'csi':{'driver':'ebs.csi.aws.com','volumeHandle':'vol-0123abc'},
                    'capacity':{'storage':'10Gi'},'persistentVolumeReclaimPolicy':'Delete'}}
        return G,claim,pv
    def test_volume_mapping_is_not_data_preservation(self):
        G,claim,pv=self.fixture();row=G.validate_pv(claim,pv)
        self.assertEqual(row['volume_id'],'vol-0123abc');self.assertFalse(row['data_preserved'])
        self.assertEqual(row['restore_volume'],'INPUT_REQUIRED')
    def test_foreign_claim_or_non_ebs_cannot_enter_snapshot_plan(self):
        G,claim,pv=self.fixture();pv['spec']['claimRef']['uid']='foreign'
        with self.assertRaisesRegex(RuntimeError,'PV_CLAIM_OWNERSHIP'):G.validate_pv(claim,pv)
        pv['spec']['claimRef']['uid']=claim['uid'];pv['spec']['csi']['volumeHandle']='../../other'
        with self.assertRaisesRegex(RuntimeError,'EXACT_EBS_CSI'):G.validate_pv(claim,pv)
    def test_automated_application_cannot_be_accepted_as_held(self):
        G,_,_=self.fixture()
        obj={'apiVersion':'argoproj.io/v1alpha1','kind':'Application','metadata':{'name':'fixture'},
             'spec':{'syncPolicy':{'automated':{}}}}
        with self.assertRaisesRegex(RuntimeError,'AUTOMATIC_APPLICATION'):G.restore_inventory([obj])
    def test_running_cloud_workload_is_not_silently_scaled_to_zero(self):
        G,_,_=self.fixture()
        obj={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'backend','namespace':'seokpan-cloud'},'spec':{'replicas':1}}
        with self.assertRaisesRegex(RuntimeError,'CLOUD_WORKLOAD_NOT_HELD'):G.restore_inventory([obj])
    def test_operator_secrets_and_manual_inputs_have_separate_actions(self):
        G,_,_=self.fixture()
        rows=G.restore_inventory([
            {'apiVersion':'v1','kind':'Secret','metadata':{'name':'argocd-secret'}},
            {'apiVersion':'v1','kind':'Secret','metadata':{'name':'github-client'}}])
        self.assertEqual(rows[0]['action'],'REGENERATE_OPERATOR_OR_TOKEN')
        self.assertEqual(rows[1]['action'],'PROTECTED_RESUPPLY_REVIEW')
        self.assertFalse(any(r['restored'] for r in rows))
    def test_incomplete_old_oauth_snapshot_stops_before_cluster_reads(self):
        G,_,_=self.fixture();get=unittest.mock.Mock()
        with self.assertRaisesRegex(RuntimeError,'ACCEPTED_OAUTH_SNAPSHOT'):
            G.capture({'contract':{'argo_namespace':'fixture'},'snapshots':[]},get)
        get.assert_not_called()

class MonitoringSnapshotBoundaries(unittest.TestCase):
    def fixture(self):
        M=load('rosa-monitoring-volume-backup')
        rows=[];volumes=[]
        for i in range(4):
            rows.append({'namespace':'openshift-monitoring','claim':'claim-'+str(i),
                'claim_uid':'uid-'+str(i),'volume_id':'vol-000'+str(i),
                'capacity':{'storage':'10Gi'},
                'node_affinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[
                    {'key':'topology.ebs.csi.aws.com/zone','operator':'In','values':['ap-northeast-2a']}]}]}},
                'filesystem':'ext4','access_modes':['ReadWriteOnce'],'volume_mode':'Filesystem','storage_class':'gp3'})
            volumes.append({'VolumeId':'vol-000'+str(i),'Encrypted':True,'KmsKeyId':'fixture-key',
                'AvailabilityZone':'ap-northeast-2a','Size':10,'State':'in-use','Tags':[
                    {'Key':'kubernetes.io/cluster/fixture-infra','Value':'owned'},
                    {'Key':'CSIVolumeName','Value':'pvc-uid-'+str(i)}]})
        return M,rows,{'Volumes':volumes}
    def test_only_exact_encrypted_claim_volumes_are_accepted(self):
        M,rows,observed=self.fixture()
        self.assertEqual(len(M.validate_sources(rows,observed,'fixture-infra')),4)
        observed['Volumes'][0]['Encrypted']=False
        with self.assertRaisesRegex(RuntimeError,'ENCRYPTED_SOURCE'):M.validate_sources(rows,observed,'fixture-infra')
    def test_foreign_volume_or_wrong_cluster_stops_before_writes(self):
        M,rows,observed=self.fixture()
        with self.assertRaisesRegex(RuntimeError,'VOLUME_CLUSTER_OWNERSHIP'):M.validate_sources(rows,observed,'other')
        observed['Volumes'].pop()
        with self.assertRaisesRegex(RuntimeError,'VOLUME_SET_CHANGED'):M.validate_sources(rows,observed,'fixture-infra')
    def test_dry_run_failure_never_reaches_non_dry_run_create(self):
        M,rows,_=self.fixture()
        def fail_third(args,out,allow_dry_run=False):
            self.assertTrue(allow_dry_run);self.assertIn('--dry-run',args)
            if args[args.index('--volume-id')+1]==rows[2]['volume_id']:raise RuntimeError('fixture-denied')
        with patch.object(M,'aws',side_effect=fail_third) as calls:
            with self.assertRaisesRegex(RuntimeError,'fixture-denied'):M.preflight_snapshot_writes(rows,'fixture',Path('.'))
            self.assertEqual(calls.call_count,3)
    def test_completed_snapshot_needs_owner_kms_and_original_volume(self):
        M,rows,observed=self.fixture();rows=M.validate_sources(rows,observed,'fixture-infra')
        snapshots=[]
        for i,row in enumerate(rows):
            row['snapshot_id']='snap-000'+str(i)
            snapshots.append({'SnapshotId':row['snapshot_id'],'OwnerId':'123456789012',
                'VolumeId':row['volume_id'],'Encrypted':True,'KmsKeyId':'fixture-key','VolumeSize':10,
                'State':'completed','Tags':[{'Key':'seokpan-preservation-run','Value':'fixture'},
                    {'Key':'seokpan-source-pvc-uid','Value':row['claim_uid']}]})
        self.assertTrue(M.validate_snapshots(rows,{'Snapshots':snapshots},'123456789012','fixture'))
        snapshots[0]['OwnerId']='999999999999'
        with self.assertRaisesRegex(RuntimeError,'SNAPSHOT_IDENTITY'):M.validate_snapshots(rows,{'Snapshots':snapshots},'123456789012','fixture')
    def test_restore_uses_new_volume_and_fresh_claim_uid(self):
        M,rows,_=self.fixture();row=dict(rows[0],size_gib=10)
        pv,pvc=M.restore_declarations(row,'vol-123abc','restored-monitoring')
        self.assertEqual(pv['spec']['csi']['fsType'],'ext4')
        self.assertEqual(pv['spec']['persistentVolumeReclaimPolicy'],'Retain')
        self.assertNotIn('uid',pv['spec']['claimRef']);self.assertNotIn('uid',pvc['metadata'])
        with self.assertRaisesRegex(RuntimeError,'OLD_ROSA_VOLUME_REPLAY'):M.restore_declarations(row,row['volume_id'],'restored-monitoring')
    def test_snapshot_unknown_attempt_marker_cannot_be_overwritten(self):
        M,_,_=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'attempt.json';M.exclusive(path,{'attempt':'fixture'})
            with self.assertRaises(FileExistsError):M.exclusive(path,{'attempt':'another'})
    def test_supplement_transfer_rejects_traversal_and_key_paths(self):
        V=load('verify-rosa-independent-supplements');root='/home/fixture/protected'
        self.assertEqual(V.accepted_path(root+'/accepted/file.sops.json',root),root+'/accepted/file.sops.json')
        for path in (root+'/../secret.sops.json','/etc/shadow',root+'/keys.txt',root+'/x;touch.sops.json'):
            with self.assertRaises(RuntimeError):V.accepted_path(path,root)
    def test_old_node_identity_is_not_carried_into_new_volume(self):
        M,rows,_=self.fixture();row=dict(rows[0],size_gib=10)
        row['node_affinity']['required']['nodeSelectorTerms'][0]['matchExpressions'][0]['key']='kubernetes.io/hostname'
        with self.assertRaisesRegex(RuntimeError,'STABLE_ZONE_AFFINITY'):M.restore_declarations(row,'vol-123abc','restored-monitoring')
    def test_restore_cannot_write_back_to_original_cluster(self):
        M,_,_=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);snapshot=d/'map.json';snapshot.write_bytes(b'cipher-fixture');snapshot.chmod(0o600)
            handoff=d/'handoff.json';handoff.write_text(json.dumps({'cluster_id':'sameoriginal','state':'ready'}));handoff.chmod(0o600)
            args=argparse.Namespace(snapshot_map=str(snapshot),snapshot_map_sha256=hashlib.sha256(b'cipher-fixture').hexdigest(),
                new_handoff=str(handoff),kubeconfig='fixture',key_file='fixture')
            bundle={'purpose':'ROSA_MONITORING_SNAPSHOT_MAP','volume_data_backed_up':True,
                'volume_preservation':{'cluster_id':'sameoriginal','account_id':'123456789012'}}
            with patch.object(M.B,'crypt',return_value=json.dumps(bundle).encode()),patch.object(M,'aws') as calls:
                with self.assertRaisesRegex(RuntimeError,'DIFFERENT_READY_NEW_ROSA'):
                    M.restore_volumes(args,{'cluster_id':'sameoriginal','account_id':'123456789012'},d,'fixture',{})
                calls.assert_not_called()
    def test_restore_review_does_not_print_secret_payload(self):
        M,_,_=self.fixture()
        gap={'restoration_inventory':[{'id':'v1|Secret|fixture|client','action':'PROTECTED_RESUPPLY_REVIEW'}],
             'snapshots':[{'kind':'Secret','metadata':{'name':'client'},'data':{'clientSecret':'fixture-sensitive'}}]}
        value=M.restoration_review(gap)
        self.assertEqual(value['protected_secret_ids'],['v1|Secret|fixture|client'])
        self.assertNotIn('fixture-sensitive',json.dumps(value));self.assertNotIn('clientSecret',json.dumps(value))

if __name__=="__main__":unittest.main()
