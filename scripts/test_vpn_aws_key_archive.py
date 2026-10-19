
# 가짜 키·가짜 SOPS 응답·임시 파일로 새 후보만 검사한다.
import sys
sys.dont_write_bytecode = True
import base64
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "archive", Path(__file__).with_name("vpn_aws_key_archive.py"))
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
PRIVATE = base64.b64encode(b"A" * 32).decode()
PUBLIC = archive.public_key(PRIVATE)

class ArchiveTests(unittest.TestCase):
    def runner(self, mismatch=False, fail=False):
        self.calls = []
        self.payload = None
        def run(command, **kwargs):
            self.calls.append((command, kwargs))
            if "--encrypt" in command:
                if fail:
                    return subprocess.CompletedProcess(command, 1, b"", b"fake error")
                self.payload = json.loads(kwargs["input"])
                body = {"wireguard_private_key": "ENC[fake]",
                        "sops": {"age": [{"recipient": r}
                                         for r in archive.RECIPIENTS]}}
            else:
                body = dict(self.payload)
                if mismatch:
                    body["gateway"] = "wrong"
            return subprocess.CompletedProcess(
                command, 0, json.dumps(body).encode(), b"")
        return run

    def build(self, runner, recipients=None):
        return archive.encrypt_and_verify(
            list(archive.RECIPIENTS) if recipients is None else recipients,
            "/fake/identity", {"SOPS_AGE_KEY_CMD": "forbidden"},
            runner=runner, factory=lambda: (PRIVATE, PUBLIC))

    def test_roundtrip_and_stdin(self):
        cipher, public = self.build(self.runner())
        self.assertEqual(public, PUBLIC)
        self.assertNotIn(PRIVATE.encode(), cipher)
        self.assertEqual(len(self.calls), 2)
        for command, kwargs in self.calls:
            self.assertNotIn(PRIVATE, " ".join(command))
            self.assertNotIn("SOPS_AGE_KEY_CMD", kwargs["env"])
        self.assertIn(PRIVATE.encode(), self.calls[0][1]["input"])

    def test_encrypt_failure_stops_before_decrypt(self):
        with self.assertRaises(ValueError):
            self.build(self.runner(fail=True))
        self.assertEqual(len(self.calls), 1)

    def test_recovery_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.build(self.runner(mismatch=True))

    def test_wrong_recipients_rejected(self):
        run = self.runner()
        with self.assertRaises(ValueError):
            self.build(run, ["wrong", "wrong"])
        self.assertEqual(self.calls, [])

    def test_existing_archive_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "archive.json"
            target.write_bytes(b"preserved")
            with self.assertRaises(ValueError):
                archive.write_new_archive(target, b"replacement")
            self.assertEqual(target.read_bytes(), b"preserved")

    @unittest.skipUnless(os.name == "posix", "Linux 소유자·권한 검사")
    def test_new_archive_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root.chmod(0o700)
            target = root / "archive.json"
            archive.write_new_archive(target, b"fake ciphertext\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.stat().st_uid, os.getuid())
            self.assertEqual(target.read_bytes(), b"fake ciphertext\n")

    def test_generation_format_with_fixed_fake_random(self):
        with patch.object(archive.os, "urandom", return_value=b"B" * 32):
            private, public = archive.generate_pair()
        raw = base64.b64decode(private, validate=True)
        self.assertEqual(len(raw), 32)
        self.assertEqual(raw[0] & 7, 0)
        self.assertEqual(raw[31] & 128, 0)
        self.assertEqual(raw[31] & 64, 64)
        self.assertEqual(archive.public_key(private), public)


    def _assert_import_blocked(self, responses):
        repo = Path(archive.__file__).resolve().parents[1]
        replies = iter([str(repo), *responses])

        def fake_git(command, **kwargs):
            return subprocess.CompletedProcess(
                command, 0,
                stdout=next(replies) + "\n",
                stderr=""
            )

        argv = [
            "vpn_aws_key_archive.py",
            "--create",
            "--expected-source",
            "a" * 40,
            "--recipients-file",
            "/unused",
        ]

        with (
            patch.object(
                archive.subprocess, "run", side_effect=fake_git
            ),
            patch.object(
                archive, "load_guard_after_verification"
            ) as loader,
            patch.object(archive.sys, "argv", argv),
            patch("builtins.print"),
        ):
            self.assertEqual(archive.main(), 2)
            loader.assert_not_called()

    def test_wrong_sha_blocks_import(self):
        self._assert_import_blocked(["b" * 40])

    def test_wrong_branch_blocks_import(self):
        self._assert_import_blocked([
            "a" * 40, "feature/unapproved"
        ])

    def test_modified_guard_blocks_import(self):
        self._assert_import_blocked([
            "a" * 40,
            "main",
            " M scripts/vpn_onprem_key_supply.py"
        ])



    def test_committed_guard_loads_successfully(self):
        # 기존 Git 저장소에서 승인된 실제 공통 모듈 확인
        # main()을 실행하지 않아 키 공급·복호화는 발생하지 않음
        script = Path(archive.__file__).resolve()

        guard = archive.load_guard_after_verification(script)

        self.assertTrue(callable(guard.verify_source))
        self.assertTrue(callable(guard.protected_file))
        self.assertEqual(
            Path(guard.__file__).resolve(),
            script.with_name("vpn_onprem_key_supply.py")
        )

    def test_modified_guard_bytes_rejected_before_execution(self):
        # 실제 파일은 수정하지 않고 읽은 소스만 가짜 값으로 교체
        # 만약 변조된 코드가 실행되면 AssertionError로 실패
        script = Path(archive.__file__).resolve()

        fake_committed = subprocess.CompletedProcess(
            ["git"],
            0,
            stdout=b"# approved source\\n",
            stderr=b""
        )

        unapproved_source = (
            b"raise AssertionError('UNAPPROVED_CODE_EXECUTED')\\n"
        )

        with (
            patch.object(
                archive.subprocess,
                "run",
                return_value=fake_committed
            ),
            patch.object(
                Path,
                "read_bytes",
                return_value=unapproved_source
            )
        ):
            with self.assertRaises(ValueError):
                archive.load_guard_after_verification(script)


if __name__ == "__main__":
    import io
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    print(json.dumps({
        "검사 수": result.testsRun,
        "실패 수": len(result.failures),
        "오류 수": len(result.errors),
        "건너뜀 수": len(result.skipped),
        "검사 성공": result.wasSuccessful(),
        "실패한 검사": [test.id().split(".")[-1]
                         for test, _ in result.failures + result.errors],
        "범위": "가짜 키·가짜 SOPS·임시 파일. 실제 키 생성·복호화·서버 없음"
    }, ensure_ascii=False))
    raise SystemExit(0 if result.wasSuccessful() else 1)
