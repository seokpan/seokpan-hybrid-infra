import base64
import copy
import io
import json
import os
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parent / "vpn_aws_install_once.py"
code = types.ModuleType("tested_install_once")
code.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), "exec"), code.__dict__)


class OnceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)

        self.session_dir = self.root / "session"
        self.session_dir.mkdir(mode=0o700)
        self.keys = self.root / "keys"
        self.keys.mkdir(mode=0o700)

        self.public = base64.b64encode(b"Q" * 32).decode()
        self.payload = {
            "purpose": "aws-gateway-wireguard",
            "gateway": "aws-vpn-gateway",
            "wireguard_private_key":
                base64.b64encode(b"T" * 32).decode(),
            "wireguard_public_key": self.public,
        }
        self.session = {
            "instance_id": "i-0123456789abcdef0",
            "nonce": "a" * 64,
            "expires": 1200,
            "archive_sha256": "b" * 64,
            "wireguard_public_key": self.public,
            "used": False,
        }
        code.record(self.session_dir, "session.json", self.session)

        self.envelope = {
            k: self.session[k] for k in code.load_receiver().FIELDS
        }
        self.envelope["ciphertext"] = (
            base64.b64encode(b"C" * 384).decode()
        )
        self.expected = {
            k: self.session[k]
            for k in (
                "instance_id",
                "archive_sha256",
                "wireguard_public_key",
            )
        }

    def run_install(self, decrypt=None, envelope=None):
        return code.install_once(
            self.session_dir,
            envelope or copy.deepcopy(self.envelope),
            1000,
            decrypt or (
                lambda cipher, label:
                json.dumps(self.payload).encode()
            ),
            lambda private: self.public,
            self.keys,
            self.expected,
        )

    def test_success_and_restart_blocked(self):
        self.run_install()
        before = (self.keys / "wg0.key").read_bytes()

        with self.assertRaises(ValueError):
            self.run_install()

        self.assertEqual(
            before, (self.keys / "wg0.key").read_bytes()
        )
        self.assertTrue(
            (self.session_dir / "install-completed.json").is_file()
        )

        records = "".join(
            p.read_text()
            for p in self.session_dir.glob("install-*.json")
        )
        self.assertNotIn(
            self.payload["wireguard_private_key"], records
        )

    def test_invalid_context_not_consumed(self):
        wrong = copy.deepcopy(self.envelope)
        wrong["nonce"] = "c" * 64

        with self.assertRaises(ValueError):
            self.run_install(envelope=wrong)

        self.assertFalse(
            (self.session_dir / "install-started.json").exists()
        )
        self.run_install()

    def test_install_failure_blocks_retry(self):
        receiver = code.load_receiver()

        with patch.object(
            code, "load_receiver", return_value=receiver
        ):
            with patch.object(
                receiver,
                "install_new_key",
                side_effect=OSError("시험 오류"),
            ):
                with self.assertRaises(RuntimeError):
                    self.run_install()

        self.assertTrue(
            (self.session_dir / "install-started.json").exists()
        )
        self.assertFalse(
            (self.session_dir / "install-completed.json").exists()
        )

        with self.assertRaises(ValueError):
            self.run_install()

    def test_interrupted_record_blocks_retry(self):
        (self.session_dir / "install-started.json").write_bytes(b"")

        with self.assertRaises(ValueError):
            self.run_install()

        self.assertFalse((self.keys / "wg0.key").exists())

    def test_concurrent_attempt_only_one_installs(self):
        barrier = threading.Barrier(2)

        def decrypt(cipher, label):
            barrier.wait(timeout=10)
            return json.dumps(self.payload).encode()

        def attempt():
            try:
                self.run_install(decrypt=decrypt)
                return True
            except FileExistsError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda _: attempt(), range(2))
            )

        self.assertEqual(sorted(results), [False, True])
        self.assertTrue(
            (self.session_dir / "install-completed.json").is_file()
        )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        OnceTests
    )
    result = unittest.TextTestRunner(
        stream=io.StringIO()
    ).run(suite)

    print(json.dumps({
        "검사 수": result.testsRun,
        "실패 수": len(result.failures),
        "오류 수": len(result.errors),
        "건너뜀 수": len(result.skipped),
        "검사 성공": result.wasSuccessful(),
        "실패한 검사": [
            test.id().split(".")[-1]
            for test, _ in result.failures + result.errors
        ],
    }, ensure_ascii=False))

    raise SystemExit(0 if result.wasSuccessful() else 1)
