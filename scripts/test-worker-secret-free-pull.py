import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("native_pull", Path(__file__).with_name("check-worker-secret-free-pull.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def secret(registry="quay.io"):
    document = json.dumps({"auths": {registry: {"auth": "VERY_SECRET_CREDENTIAL"}}}).encode()
    return {"data": {".dockerconfigjson": base64.b64encode(document).decode()}}


class FakeClient:
    def __init__(self, masked_global=False, masked_pod=False):
        self.objects = {}
        self.created = []
        self.deletes = []
        self.masked_global, self.masked_pod = masked_global, masked_pod

    def get(self, args):
        if args[0] == "serviceaccounts":
            return {"items": [copy.deepcopy(v) for (k, n), v in self.objects.items() if k == "serviceaccount"]}
        if args[0] == "secret":
            return secret(m.REGISTRY if self.masked_global or args[1] == "injected-ecr" else "quay.io")
        if args[0] == "nodes":
            return {"items": [{"metadata": {"name": "worker-" + str(n), "labels": {"node-role.kubernetes.io/worker": "", "kubernetes.io/hostname": "worker-" + str(n)}}, "status": {"images": [{"names": [m.IMAGES["backend"]]}]}} for n in range(3)]}
        if args[0] == "pods":
            pods = []
            for (kind, name), obj in self.objects.items():
                if kind != "pod":continue
                obj = copy.deepcopy(obj)
                obj["spec"]["nodeName"] = obj["spec"]["nodeSelector"]["kubernetes.io/hostname"]
                if self.masked_pod:obj["spec"]["imagePullSecrets"] = [{"name": "injected-ecr"}]
                obj["status"] = {"phase": "Succeeded", "containerStatuses": [{"imageID": "fixture@sha256:resolved", "state": {"terminated": {"exitCode": 0}}}]}
                pods.append(obj)
            return {"items": pods}
        return copy.deepcopy(self.objects.get((args[0], args[1])))

    def create(self, obj):
        obj = copy.deepcopy(obj)
        kind = "pod" if obj["kind"] == "Pod" else "serviceaccount"
        obj["metadata"]["uid"] = "uid-" + obj["metadata"]["name"]
        self.objects[kind, obj["metadata"]["name"]] = obj
        self.created.append(obj)
        return copy.deepcopy(obj)

    def run(self, args, payload=None):
        self.deletes.append((args, json.loads(payload)))
        plural, name = args[1].split("/")[-2:]
        kind = "pod" if plural == "pods" else "serviceaccount"
        current = self.objects[kind, name]
        assert current["metadata"]["uid"] == json.loads(payload)["preconditions"]["uid"]
        del self.objects[kind, name]
        return b"{}"


class NativePullTests(unittest.TestCase):
    def test_local_source_reader_matches_declared_api_and_never_outputs_credentials(self):
        original_spec = importlib.util.spec_from_file_location("source_reader", Path(__file__).with_name("resume-cutover-independent.py"))
        original = importlib.util.module_from_spec(original_spec)
        original_spec.loader.exec_module(original)
        for expected, should_query in (("https://original.invalid:6443", True), ("https://different.invalid:6443", False)):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); (root / ".kube").mkdir(); (root / ".kube/config").write_text("fixture")
                commands = []
                def command(args, **kwargs):
                    commands.append(args)
                    if "config" in args:
                        value = {"clusters": [{"name": "original", "cluster": {"server": "https://original.invalid:6443"}}], "contexts": [{"name": "admin", "context": {"cluster": "original"}}], "users": [{"user": {"token": "VERY_SECRET_CREDENTIAL"}}]}
                    elif "deployment" in args:
                        value = {"metadata": {"uid": "original-dep"}, "spec": {"replicas": 2}}
                    elif "application.argoproj.io" in args:
                        value = {"metadata": {}, "spec": {"source": {"repoURL": "https://github.com/seokpan/seokpan-gitops.git", "path": "apps/backend"}}}
                    else:
                        value = {"items": []}
                    return types.SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())
                with patch.dict(os.environ, {"KUBECONFIG": ""}), patch("pathlib.Path.home", return_value=root), patch("shutil.which", return_value="fixture-kubectl"), patch("subprocess.run", side_effect=command), contextlib.redirect_stdout(io.StringIO()) as output:
                    exec(compile(original.REMOTE_READER, "local-source-reader", "exec"), {"CLOUD_API": "https://cloud.invalid", "SOURCE_API": expected})
                self.assertEqual(any("get" in args for args in commands), should_query)
                self.assertNotIn("VERY_SECRET_CREDENTIAL", output.getvalue())
                self.assertFalse(any("--raw" in args or "apply" in args or "scale" in args for args in commands))

    def test_matching_registry_and_wildcard_credentials_are_detected(self):
        self.assertTrue(m.has_registry_credentials(secret(m.REGISTRY)))
        self.assertTrue(m.has_registry_credentials(secret("*.dkr.ecr.ap-northeast-2.amazonaws.com")))
        self.assertFalse(m.has_registry_credentials(secret()))

    def test_probe_never_starts_application_or_mounts_credentials(self):
        node = {"metadata": {"labels": {"kubernetes.io/hostname": "worker"}}}
        pod = m.probe("fixture", node, "backend", "account", "run")
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertEqual(pod["spec"]["imagePullSecrets"], [])
        self.assertEqual(pod["spec"]["containers"][0]["command"], ["/bin/sh", "-c", "exit 0"])
        self.assertNotIn("env", pod["spec"]["containers"][0])

    def test_replaced_resource_is_preserved(self):
        client = FakeClient()
        client.objects["pod", "fixture"] = {"metadata": {"uid": "replacement", "labels": {m.RUN_LABEL: "run"}}}
        with self.assertRaisesRegex(m.Stop, "CLEANUP_IDENTITY_CHANGED"):
            m.cleanup_one(client, "pod", "fixture", "old-uid", "run")
        self.assertEqual(client.deletes, [])

    def test_six_probes_cleanup_by_uid_and_do_not_overclaim_credential_trace(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()) as output:
            folder = Path(temporary)
            self.assertEqual(m.execute(client, folder), 0)
            result = json.loads((folder / "receipt.json").read_text())
            self.assertEqual(len(result["results"]), 6)
            self.assertEqual(len(client.deletes), 7)
            self.assertEqual(client.objects, {})
            self.assertIn("NOT_INDEPENDENTLY_OBSERVED", result["worker_role_credential_causal_trace"])
            self.assertNotIn("VERY_SECRET_CREDENTIAL", output.getvalue())
            self.assertEqual(result["sql_calls"], 0)

    def test_global_matching_secret_stops_before_pod_creation(self):
        client = FakeClient(masked_global=True)
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.execute(client, Path(temporary)), 1)
            result = json.loads((Path(temporary) / "receipt.json").read_text())
        self.assertIn("GLOBAL_ECR_PULL_SECRET", result["reason"])
        self.assertEqual(client.created, [])

    def test_admission_injected_matching_secret_cannot_pass_as_native(self):
        client = FakeClient(masked_pod=True)
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.execute(client, Path(temporary)), 1)
            result = json.loads((Path(temporary) / "receipt.json").read_text())
        self.assertIn("ADMITTED_ECR_PULL_SECRET", result["reason"])
        self.assertEqual(client.objects, {})

    def test_infra_nodes_do_not_count_as_compute_targets(self):
        document = FakeClient().get(["nodes"])
        extra = copy.deepcopy(document["items"][0])
        extra["metadata"]["labels"]["node-role.kubernetes.io/infra"] = ""
        document["items"].append(extra)
        self.assertEqual(len(m.compute_nodes(document)), 3)


if __name__ == "__main__":
    unittest.main()
