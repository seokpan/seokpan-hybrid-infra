import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name("collect-cutover-readiness.py")


class ReadOnlyDiscoveryTests(unittest.TestCase):
    def exercise(self, *, cloud=False, correct_source=True, accessible=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = root / "completed"; previous.mkdir()
            held = root / "held"; held.mkdir()
            config = root / "kubeconfig"; config.write_text("local fixture")
            (previous / "pool-receipt.json").write_text(json.dumps({"total_budget":38,"runtime_adopted":False}))
            (previous / "sg-receipt.json").write_text(json.dumps({"foundation_plan_read_access_pending":True}))
            (held / "held-render.json").write_text(json.dumps({"items":[{"kind":"Deployment","metadata":{"name":"backend"},"spec":{"template":{"spec":{"containers":[{"name":"backend"}]}}}}]}))
            calls = []
            server = "https://cloud.invalid:6443" if cloud else "https://original.invalid:6443"
            repo = "https://github.com/seokpan/seokpan-gitops.git" if correct_source else "https://github.com/example/unrelated"
            def fake_run(command, **kwargs):
                calls.append(command)
                if "config" in command:
                    value = {"clusters":[{"name":"fixture","cluster":{"server":server}}],"contexts":[{"name":"fixture","context":{"cluster":"fixture"}}]}
                elif not accessible:
                    return subprocess.CompletedProcess(command, 1, b"", b"Forbidden")
                elif "deployment" in command:
                    value = {"metadata":{"namespace":"application","uid":"deployment-fixture"},"spec":{"replicas":2,"template":{"spec":{"containers":[{"name":"backend","image":"approved@sha256:fixture"}]}}}}
                elif "application.argoproj.io" in command:
                    value = {"metadata":{"uid":"application-fixture"},"spec":{"source":{"repoURL":repo,"path":"apps/backend","targetRevision":"main"},"syncPolicy":{"automated":{"selfHeal":True}}}}
                else:
                    value = {"items":[]}
                return subprocess.CompletedProcess(command, 0, json.dumps(value).encode(), b"")
            arguments = [str(SOURCE), str(root), str(previous), str(held), "--cloud-api", "https://cloud.invalid:6443"]
            old_mask = os.umask(0o077)
            try:
                with patch.object(sys, "argv", arguments), patch.dict(os.environ, {"KUBECONFIG":str(config)}), patch("pathlib.Path.home", return_value=root), patch("shutil.which", return_value="fixture-oc"), patch("subprocess.run", side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()) as output:
                    runpy.run_path(str(SOURCE), run_name="__main__")
                receipts = list(root.glob("cloud-cutover-readiness-*/receipt.json"))
                self.assertEqual(len(receipts), 1)
                result = json.loads(receipts[0].read_text())
                self.assertNotIn("local fixture", output.getvalue())
                for command in calls:
                    self.assertFalse(set(command) & {"apply","patch","scale","delete","create","exec","sync","--raw"})
                self.assertFalse(result["writer_stop_ready"])
                self.assertEqual(result["cluster_mutations"], 0)
                self.assertEqual(result["sql_calls"], 0)
                return result, calls
            finally:
                os.umask(old_mask)

    def test_original_contract_triggers_writer_inventory_but_never_stop(self):
        result, calls = self.exercise()
        self.assertEqual(result["original_cluster_contract_match_count"], 1)
        self.assertTrue(any("hpa,jobs,cronjobs" in call for call in calls))
        self.assertFalse(result["pool_runtime_adopted"])

    def test_cloud_context_is_not_queried(self):
        result, calls = self.exercise(cloud=True)
        self.assertEqual(result["original_cluster_contract_match_count"], 0)
        self.assertFalse(any("get" in call for call in calls))

    def test_other_repo_never_triggers_writer_inventory(self):
        result, calls = self.exercise(correct_source=False)
        self.assertEqual(result["original_cluster_contract_match_count"], 0)
        self.assertFalse(any("hpa,jobs,cronjobs" in call for call in calls))

    def test_access_failure_stays_unverified(self):
        result, calls = self.exercise(accessible=False)
        self.assertEqual(result["original_cluster_contract_match_count"], 0)
        self.assertEqual(result["contexts"][0]["read_status"], "TARGET_NOT_FOUND_OR_ACCESS_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
