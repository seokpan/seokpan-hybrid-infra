"""Offline failure, login isolation and compare-and-swap coverage."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("native_runtime", Path(__file__).with_name("prepare-native-runtime-pull.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
API = "https://cloud.example:6443"


def config():
    return {"current-context": "old", "contexts": [{"name": "old", "context": {"cluster": "old"}}],
            "clusters": [{"name": "old", "cluster": {"server": API, "certificate-authority-data": "VERIFIED_CA"}}],
            "users": [{"name": "old", "user": {"token": "OLD_TOKEN"}}]}


class NativeRuntimeTests(unittest.TestCase):
    def test_failure_classes_do_not_reveal_raw_values(self):
        for raw, expected in [(b"Unauthorized private-value", "AUTHENTICATION_REQUIRED"),
                              (b"Forbidden private-value", "ACCESS_DENIED"),
                              (b"x509: private-value", "TLS_TRUST_FAILED"),
                              (b"dial tcp: no such host private-value", "API_UNREACHABLE"),
                              (b"Error from server (NotFound)", "SERVICE_ACCOUNT_NOT_FOUND")]:
            self.assertEqual(m.command_failure(raw), expected)

    def test_personal_config_keeps_trust_and_discards_old_users(self):
        old = config()
        result = m.personal_config(old, API, "PERSONAL_TOKEN")
        self.assertEqual(result["clusters"][0]["cluster"]["certificate-authority-data"], "VERIFIED_CA")
        self.assertNotIn("OLD_TOKEN", json.dumps(result))
        self.assertEqual(old["users"][0]["user"]["token"], "OLD_TOKEN")

    def test_personal_config_rejects_other_server_insecure_or_whitespace(self):
        for variant in ("wrong", "insecure", "token"):
            document = config()
            if variant == "insecure":
                document["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = True
            with self.assertRaises(RuntimeError):
                m.personal_config(document, "https://wrong" if variant == "wrong" else API,
                                  "TOKEN EXTRA" if variant == "token" else "TOKEN")

    def execute(self, error=None, login=False, owner=False, conflict=False, unsafe_resume=False,
                identity_error=None, identity_user="tjung03", saved_login=False, token_input="PERSONAL_TOKEN"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proof = {"status": "PASS_SECRET_FREE_WORKER_PULL_AND_EXECUTION", "cleanup": "PASS_OWN_PODS_AND_SERVICEACCOUNT_REMOVED",
                     "results": [{"repository": repo, "compute_node_number": number, "succeeded": True}
                                 for repo in ("backend", "frontend") for number in (1, 2, 3)]}
            bundle = {"items": [{"kind": "Deployment", "metadata": {"name": name, "namespace": m.NS},
                                  "spec": {"replicas": 0, "template": {"spec": {"serviceAccountName": m.SA,
                                  "imagePullSecrets": [{"name": m.TEMP_SECRET}, {"name": "keep-me"}]}}}}
                                for name in ("backend", "frontend")]}
            for name, value in (("proof.json", proof), ("held.json", bundle), ("kubeconfig", config())):
                (root / name).write_text(json.dumps(value))
            previous = root / "previous"
            previous.mkdir()
            (previous / "receipt.json").write_text(json.dumps({"status": "STOP_NATIVE_RUNTIME_REFERENCE", "service_account_patch_attempted": unsafe_resume}))
            if saved_login:
                (previous / "personal-kubeconfig").write_text(json.dumps(config()))
            account = {"metadata": {"uid": "same-uid", "resourceVersion": "1"},
                       "imagePullSecrets": [{"name": m.TEMP_SECRET}, {"name": "keep-me"}]}
            if owner:
                account["metadata"]["annotations"] = {"argocd.argoproj.io/tracking-id": "owner"}
            calls, patches = [], []
            failed = False

            def run(argv, **kwargs):
                nonlocal failed
                calls.append(argv[3:])
                command = argv[3:]
                if command == ["whoami", "--show-server"]:
                    payload = API
                elif command[:2] == ["config", "view"]:
                    payload = json.dumps(config())
                elif command == ["whoami"]:
                    if identity_error:
                        return subprocess.CompletedProcess(argv, 1, b"", identity_error)
                    payload = identity_user
                elif command[:2] == ["get", "serviceaccount"]:
                    if error and not failed:
                        failed = True
                        return subprocess.CompletedProcess(argv, 1, b"", error)
                    payload = json.dumps(account)
                elif command[:2] == ["patch", "serviceaccount"]:
                    document = json.loads(Path(command[-1]).read_text())
                    patches.append(document)
                    if conflict:
                        return subprocess.CompletedProcess(argv, 1, b"", b"test operation failed")
                    account["imagePullSecrets"] = document[-1]["value"]
                    payload = "{}"
                else:
                    raise AssertionError(command)
                return subprocess.CompletedProcess(argv, 0, payload.encode(), b"")

            args = ["tool", str(root), "--native-proof", str(root / "proof.json"), "--held-bundle", str(root / "held.json"),
                    "--kubeconfig", str(root / "kubeconfig"), "--cloud-api", API, "--resume-from", str(previous), "--apply"]
            if login:
                args.append("--reauthenticate-if-required")
            output = io.StringIO()
            with patch.object(m.sys, "argv", args), patch.object(m.shutil, "which", return_value=str(root / "kubeconfig")), \
                    patch.object(m.subprocess, "run", side_effect=run), patch.object(m.sys.stdin, "isatty", return_value=True), \
                    patch.object(m.getpass, "getpass", return_value=token_input) as prompt, contextlib.redirect_stdout(output):
                code = m.main()
            folder = next(root.glob("cloud-runtime-native-*"))
            receipt = json.loads((folder / "receipt.json").read_text())
            self.assertNotIn("PERSONAL_TOKEN", output.getvalue())
            self.assertEqual(json.loads((root / "kubeconfig").read_text()), config())
            return code, receipt, calls, patches, prompt.call_count

    def test_unauthorized_without_login_stops_before_patch(self):
        code, receipt, _, patches, prompts = self.execute(error=b"Unauthorized")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["diagnostic"]["operation"], "CURRENT_RUNTIME_SERVICE_ACCOUNT")
        self.assertEqual(receipt["reason"], "AUTHENTICATION_REQUIRED")
        self.assertEqual((patches, prompts), ([], 0))

    def test_forbidden_never_prompts_or_patches(self):
        _, receipt, _, patches, prompts = self.execute(error=b"Forbidden", login=True)
        self.assertEqual(receipt["reason"], "ACCESS_DENIED")
        self.assertEqual((patches, prompts), ([], 0))

    def test_explicit_personal_login_then_cas_patch_preserves_other_reference(self):
        code, receipt, _, patches, prompts = self.execute(error=b"Unauthorized", login=True)
        self.assertEqual(code, 0)
        self.assertTrue(receipt["authentication_refresh_attempted"])
        self.assertEqual(prompts, 1)
        self.assertEqual(patches[0][0]["path"], "/metadata/resourceVersion")
        self.assertEqual(patches[0][-1]["value"], [{"name": "keep-me"}])

    def test_gitops_owner_stops_before_patch(self):
        _, receipt, _, patches, _ = self.execute(owner=True)
        self.assertEqual(receipt["reason"], "SERVICE_ACCOUNT_GITOPS_OWNER_REVIEW_REQUIRED")
        self.assertEqual(patches, [])

    def test_conflict_records_patch_attempt_and_does_not_repeat(self):
        _, receipt, _, patches, _ = self.execute(conflict=True, login=True)
        self.assertTrue(receipt["service_account_patch_attempted"])
        self.assertEqual(len(patches), 1)

    def test_resume_rejects_previous_possible_mutation_without_any_api_call(self):
        _, receipt, calls, patches, _ = self.execute(unsafe_resume=True)
        self.assertEqual(receipt["reason"], "RESUME_REQUIRES_CONFIRMED_NO_PATCH")
        self.assertEqual((calls, patches), ([], []))

    def test_rejected_new_token_has_its_own_diagnostic_not_user_mismatch(self):
        _, receipt, _, patches, _ = self.execute(error=b"Unauthorized", login=True, identity_error=b"Unauthorized private value")
        self.assertEqual(receipt["phase"], "PERSONAL_IDENTITY_VERIFICATION")
        self.assertEqual(receipt["reason"], "PERSONAL_LOGIN_AUTHENTICATION_REQUIRED")
        self.assertEqual(receipt["diagnostic"]["operation"], "PERSONAL_IDENTITY_VERIFICATION")
        self.assertEqual(patches, [])

    def test_other_logged_in_user_is_distinct_and_never_patched(self):
        _, receipt, _, patches, _ = self.execute(error=b"Unauthorized", login=True, identity_user="cluster-admin")
        self.assertEqual(receipt["reason"], "PERSONAL_USER_MISMATCH")
        self.assertEqual(receipt["personal_user"], "cluster-admin")
        self.assertEqual(patches, [])

    def test_saved_working_personal_config_resumes_without_new_token(self):
        code, receipt, _, _, prompts = self.execute(saved_login=True)
        self.assertEqual(code, 0)
        self.assertIn("previous", receipt["kubeconfig_file"])
        self.assertEqual(prompts, 0)

    def test_login_command_is_parsed_but_never_executed(self):
        line = "oc login --token='PERSONAL_TOKEN' --server=" + API
        self.assertEqual(m.token_from_input(line, API), "PERSONAL_TOKEN")
        self.assertEqual(self.execute(error=b"Unauthorized", login=True, token_input=line)[0], 0)

    def test_wrong_server_or_shell_commands_are_rejected(self):
        for line in ("oc login --token=PERSONAL_TOKEN --server=https://elsewhere", "oc login --token=x --server=" + API + " --insecure-skip-tls-verify", "echo PERSONAL_TOKEN", "oc login --token=x --server=" + API + " ; echo x"):
            with self.assertRaises(RuntimeError):
                m.token_from_input(line, API)


if __name__ == "__main__":
    unittest.main()
