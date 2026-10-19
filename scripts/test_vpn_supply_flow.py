# 실제 키·SOPS·SSH·AWS 없이 실행 흐름과 임시 파일 영향만 검사한다.
import sys
sys.dont_write_bytecode = True
import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

controller = load("controller_test", SCRIPTS / "vpn_onprem_key_supply.py")
remote = load("remote_test", SCRIPTS / "vpn_key_install_remote.py")

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "scripts/vpn_key_install_remote.py").write_text(
            "# 시험용 공급 프로그램\n", encoding="utf-8")
        archive = self.home / ".local/share/seokpan-hybrid/secrets/vpn"
        archive.mkdir(parents=True)
        self.data = b"fake archive"
        (archive / "onprem-gateway-wireguard.sops.json").write_bytes(self.data)
        self.payload = {
            "purpose": "onprem-gateway-wireguard",
            "gateway": "vpn-gw-01",
            "wireguard_private_key": base64.b64encode(b"A" * 32).decode(),
            "wireguard_public_key": base64.b64encode(b"B" * 32).decode(),
        }
        self.decrypted = subprocess.CompletedProcess(
            ["fake-sops"], 0, json.dumps(self.payload).encode(), b"")

    def invoke(self, effects, gate_error=False, apply=True):
        import hashlib
        output = io.StringIO()
        argv = ["supply", "--apply", "--expected-source", "1" * 40]
        gate = {"side_effect": ValueError("가짜 비밀 원문")} if gate_error else {
            "return_value": (self.repo, {})}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", argv if apply else ["supply"]))
            stack.enter_context(patch.object(controller, "os", types.SimpleNamespace(name="posix")))
            stack.enter_context(patch.object(Path, "home", return_value=self.home))
            stack.enter_context(patch.object(controller, "verify_source", **gate))
            stack.enter_context(patch.object(controller, "protected_file"))
            stack.enter_context(patch.object(
                controller, "ARCHIVE_HASH", hashlib.sha256(self.data).hexdigest()))
            stack.enter_context(patch.dict(sys.modules, {"vpn_key_install_remote": remote}))
            run = stack.enter_context(patch.object(
                controller.subprocess, "run", side_effect=effects))
            stack.enter_context(contextlib.redirect_stdout(output))
            result = controller.main()
        return result, output.getvalue(), run

    def test_default_does_not_run_commands(self):
        result, _, run = self.invoke([], apply=False)
        self.assertEqual(result, 0)
        run.assert_not_called()

    def test_gate_failure_does_not_decrypt_or_connect(self):
        result, output, run = self.invoke([], gate_error=True)
        self.assertEqual(result, 2)
        run.assert_not_called()
        self.assertIn("실행 저장소 확인", output)
        self.assertIn("서버 변경이 없습니다", output)
        self.assertNotIn("가짜 비밀 원문", output)

    def test_decryption_failure_is_before_ssh(self):
        result, output, run = self.invoke([
            subprocess.CompletedProcess(["fake-sops"], 1, b"", b"fake secret")])
        self.assertEqual(result, 2)
        self.assertEqual(run.call_count, 1)
        self.assertIn("기존 암호문 복호화", output)
        self.assertIn("서버 변경이 없습니다", output)
        self.assertNotIn("fake secret", output)

    def test_uncertain_ssh_requires_state_check(self):
        result, output, run = self.invoke([
            self.decrypted, subprocess.TimeoutExpired("ssh", 60)])
        self.assertEqual(result, 2)
        self.assertEqual(run.call_count, 2)
        self.assertIn("부분 공급 여부", output)
        self.assertNotIn("서버 변경이 없습니다", output)

    def test_ssh_options_and_secret_transport(self):
        response = json.dumps({"결과": "기존 키 복원·공급 완료"}).encode()
        result, output, run = self.invoke([
            self.decrypted, subprocess.CompletedProcess(["fake-ssh"], 0, response, b"")])
        self.assertEqual(result, 0)
        args, kwargs = run.call_args
        self.assertIn("StrictHostKeyChecking=yes", args[0])
        private = self.payload["wireguard_private_key"]
        self.assertNotIn(private, " ".join(args[0]))
        self.assertIn(private.encode(), kwargs["input"])
        self.assertNotIn(private, output)

    def test_python_cache_does_not_dirty_repository(self):
        repo = self.home / "cache_repo"
        (repo / "scripts").mkdir(parents=True)
        script = repo / "scripts/vpn_onprem_key_supply.py"
        script.write_text("# 시험용 진입점\n", encoding="utf-8")
        (repo / "scripts/cache_probe.py").write_text("value = 1\n", encoding="utf-8")
        (repo / ".gitignore").write_bytes((SCRIPTS.parent / ".gitignore").read_bytes())
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        def git(*args):
            return subprocess.run(
                ["git", "-c", "core.autocrlf=false", *args], cwd=str(repo),
                env=env, capture_output=True, text=True, timeout=15,
                check=True).stdout.strip()
        git("init", "-q", "--initial-branch=main")
        git("add", "--", ".")
        git("-c", "user.name=Flow Test", "-c", "user.email=flow@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", "시험 자료")
        sha = git("rev-parse", "HEAD")
        subprocess.run(
            [sys.executable, "-I", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import cache_probe",
             str(repo / "scripts")],
            env=env, capture_output=True, timeout=15, check=True)
        self.assertTrue(list((repo / "scripts/__pycache__").glob("*.pyc")))
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all"), "")
        actual, _ = controller.verify_source(script, sha)
        self.assertEqual(actual, repo.resolve())

if __name__ == "__main__":
    unittest.main(verbosity=2)
