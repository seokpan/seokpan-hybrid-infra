import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
import contextlib
import io
import subprocess
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("cutover_resume", Path(__file__).with_name("resume-cutover-independent.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FakeCloud:
    def __init__(self, expires="2999-01-01T00:00:00+00:00", native=False):
        self.calls = []
        self.expires = expires
        self.native = native

    def get(self, args, label):
        self.calls.append(("get", args))
        if args[1] == "secret":
            return {"type":"kubernetes.io/dockerconfigjson","metadata":{"labels":{"app.kubernetes.io/part-of":"seokpan","seokpan.io/environment":"cloud"},"annotations":{"seokpan.io/pull-expires-at":self.expires}},"data":{".dockerconfigjson":"NEVER_PRINT_THIS"}}
        if args[1] == "serviceaccount":
            return {"imagePullSecrets": [] if self.native else [{"name": "seokpan-cloud-ecr-pull"}]}
        return {"items":[]}

    def run(self, args, *unused, **kwargs):
        self.calls.append(("run", args))
        if "apply" in args:
            assert "--dry-run=server" in args
        return types.SimpleNamespace(stdout=b"{}")


class IndependentStagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def resource_inputs(self, replicas=0):
        held, pool, result = [self.root / n for n in ("held","pool","result")]
        for p in (held,pool,result):p.mkdir()
        objects = [{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":n,"namespace":"seokpan-cloud"},"spec":{"replicas":replicas,"template":{"spec":{"containers":[{"name":n,"image":"digest-unchanged","env":[{"name":"PASSWORD_REF","valueFrom":{"secretKeyRef":{"name":"existing","key":"value"}}}]}]}}}} for n in ("backend","frontend")]
        (held/"held-render.json").write_text(json.dumps({"apiVersion":"v1","kind":"List","items":objects}))
        container = {"name":"backend","command":["uvicorn"],"args":["--workers","1"],"env":[{"name":"SEOKPAN_DATABASE_POOL_SIZE","value":"2"}]}
        (pool/"backend-pool.DO_NOT_APPLY.candidate.json").write_text(json.dumps({"spec":{"strategy":{"type":"RollingUpdate","rollingUpdate":{"maxSurge":1,"maxUnavailable":0}},"template":{"spec":{"containers":[container]}}}}))
        return held,pool,result

    def test_two_changed_deployments_dry_run_and_secret_refs_preserved(self):
        held,pool,result = self.resource_inputs()
        cloud = FakeCloud()
        answer = m.resources_stage(cloud,held,pool,result)
        candidate = json.loads((result/"held-with-resources-and-pool.DO_NOT_APPLY.json").read_text())
        self.assertEqual(answer["objects_persisted"],0)
        self.assertFalse(answer["runtime_pool_adopted"])
        for obj in candidate["items"]:
            self.assertEqual(obj["spec"]["replicas"],0)
            c=obj["spec"]["template"]["spec"]["containers"][0]
            self.assertEqual(c["image"],"digest-unchanged")
            self.assertEqual(c["env"][0]["valueFrom"]["secretKeyRef"]["name"],"existing")
            self.assertIn("limits",c["resources"])
        self.assertEqual(sum("apply" in args for kind,args in cloud.calls if kind=="run"),1)

    def test_nonzero_candidate_rejected_before_admission(self):
        held,pool,result = self.resource_inputs(1)
        cloud=FakeCloud()
        with self.assertRaisesRegex(m.Failure,"HELD_DEPLOYMENT_REQUIRED"):
            m.resources_stage(cloud,held,pool,result)
        self.assertEqual(cloud.calls,[])

    def test_inventory_candidates_are_not_ssh_targets(self):
        ssh=self.root/".ssh";ssh.mkdir()
        (ssh/"config").write_text("Host cp-01 *\n  IdentityFile private\n")
        with patch.object(m.subprocess,"run") as command:
            result=m.source_inventory(self.root,"","https://cloud.invalid",self.root)
        command.assert_not_called()
        self.assertEqual(result["ssh_aliases"],["cp-01"])
        self.assertFalse(result["automatic_ssh_to_candidates"])

    def test_target_option_injection_rejected(self):
        with self.assertRaisesRegex(m.Failure,"SOURCE_SSH_TARGET_FORMAT"),patch.object(m.subprocess,"run") as command:
            m.source_inventory(self.root,"root@cp-01;echo bad","https://cloud.invalid",self.root)
        command.assert_not_called()

    def test_explicit_source_uses_fixed_command_strict_host_key_and_api_guard(self):
        output=types.SimpleNamespace(returncode=0,stdout=b'{"status":"SOURCE_SSH_READ_COMPLETED"}',stderr=b"")
        with patch.object(m.subprocess,"run",return_value=output) as command:
            m.source_inventory(self.root,"root@cp-01","https://cloud.invalid",self.root,"https://source.invalid")
        args=command.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes",args)
        self.assertEqual(args[-1],"python3 -")
        self.assertIn(b"SOURCE_API=",command.call_args.kwargs["input"])
        self.assertNotIn("--raw",args)

    def test_current_lease_is_reused_without_helper_or_mutation(self):
        cloud=FakeCloud()
        result=m.pull_stage(cloud,self.root/"missing","seokpan-cloud","account","user","image",True,self.root)
        self.assertFalse(result["secret_changed"])
        self.assertEqual(len(cloud.calls),1)
        self.assertNotIn("NEVER_PRINT_THIS",json.dumps(result))

    def test_expired_lease_needs_explicit_renew_flag(self):
        cloud=FakeCloud("2000-01-01T00:00:00+00:00")
        with self.assertRaisesRegex(m.Failure,"RENEWAL_NOT_ENABLED"):
            m.pull_stage(cloud,self.root/"missing","seokpan-cloud","account","user","image",False,self.root)

    def test_expired_lease_rejects_modified_helper(self):
        cloud=FakeCloud("2000-01-01T00:00:00+00:00")
        helper=self.root/"helper.py";helper.write_text("untrusted")
        with self.assertRaisesRegex(m.Failure,"ECR_HELPER_HASH_MISMATCH"):
            m.pull_stage(cloud,helper,"seokpan-cloud","account","user","image",True,self.root)

    def test_expired_lease_calls_only_pinned_renewal_contract(self):
        cloud=FakeCloud("2000-01-01T00:00:00+00:00")
        helper=self.root/"helper.py";helper.write_text("checked fixture")
        def configure(r,ns,account,user,image):
            r.calls.append(("renew",[ns,account,user,image]))
            return {"expires_at_utc":"2999-01-01T00:00:00+00:00"}
        loader=types.SimpleNamespace(exec_module=lambda module:None)
        module=types.SimpleNamespace(configure_ecr_pull=configure)
        digest=m.hashlib.sha256(helper.read_bytes()).hexdigest()
        with patch.object(m,"ECR_HELPER_SHA",digest),patch.object(m.importlib.util,"spec_from_file_location",return_value=types.SimpleNamespace(loader=loader)),patch.object(m.importlib.util,"module_from_spec",return_value=module):
            result=m.pull_stage(cloud,helper,"seokpan-cloud","account","user","image",True,self.root)
        self.assertTrue(result["secret_changed"])
        self.assertEqual(result["app_pods_created"],0)
        self.assertEqual(sum(kind=="renew" for kind,args in cloud.calls),1)

    def test_legacy_renewal_cannot_relink_an_accepted_native_runtime(self):
        cloud = FakeCloud("2000-01-01T00:00:00+00:00", native=True)
        with self.assertRaisesRegex(m.Failure, "NATIVE_RUNTIME_DETACHED_NO_LEGACY_PULL_RELINK"), patch.object(m.importlib.util, "spec_from_file_location") as load:
            m.pull_stage(cloud, self.root / "missing", "seokpan-cloud", "account", "user", "image", True, self.root)
        load.assert_not_called()
        self.assertFalse(any(kind in ("run", "renew") for kind, _ in cloud.calls))

    def test_remote_reader_filters_api_and_namespace_before_writer_inventory(self):
        config = self.root / "config"
        config.write_text("PRIVATE_NOT_READ")
        for namespace in ("argocd", "elsewhere"):
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                if "config" in args:
                    result = {"clusters": [{"name": name, "cluster": {"server": server}} for name, server in (("allowed", "https://source.invalid"), ("other", "https://unrelated.invalid"))],
                              "contexts": [{"name": name, "context": {"cluster": name}} for name in ("allowed", "other")]}
                elif "deployment" in args:
                    result = {"metadata": {"namespace": "application", "uid": "deployment"}, "spec": {"replicas": 2}}
                elif "application.argoproj.io" in args:
                    result = {"metadata": {"namespace": namespace}, "spec": {"source": {"repoURL": "https://github.com/seokpan/seokpan-gitops.git", "path": "apps/backend"}}}
                else:
                    result = {"items": []}
                return subprocess.CompletedProcess(args, 0, json.dumps(result).encode(), b"")
            with patch.dict(m.os.environ, {"KUBECONFIG": str(config)}), patch.object(m.Path, "home", return_value=self.root), \
                 patch.object(m.shutil, "which", return_value="fixture-kubectl"), patch.object(m.subprocess, "run", side_effect=command), contextlib.redirect_stdout(io.StringIO()) as output:
                exec(m.REMOTE_READER, {"CLOUD_API": "https://cloud.invalid", "SOURCE_API": "https://source.invalid"})
            result = json.loads(output.getvalue())
            self.assertEqual(result["contexts"][0]["source_contract_match"], namespace == "argocd")
            self.assertFalse(any("other" in call for call in calls if "get" in call))
            self.assertEqual(any("hpa,jobs,cronjobs" in call for call in calls), namespace == "argocd")


if __name__ == "__main__":
    unittest.main()
