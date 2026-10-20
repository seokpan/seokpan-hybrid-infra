import base64
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "vpn_aws_session_prepare.py"

if not SOURCE.is_file() or SOURCE.is_symlink():
    raise SystemExit("STOP: 수신 세션 소스 확인 실패")

spec = importlib.util.spec_from_file_location(
    "vpn_session_candidate", SOURCE
)
if spec is None or spec.loader is None:
    raise SystemExit("STOP: 소스 로딩 실패")

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SessionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            prefix="v04-session-test-",
            dir=str(ROOT),
        )
        self.addCleanup(self.tmp.cleanup)

        self.parent = Path(self.tmp.name)
        self.instance = "i-0123456789abcdef0"
        self.archive = hashlib.sha256(
            b"offline-test"
        ).hexdigest()
        self.public = base64.b64encode(
            bytes(range(1, 33))
        ).decode("ascii")
        self.now = 2000000000

    def prepare(self, **changes):
        values = {
            "parent": self.parent,
            "approved_instance": self.instance,
            "observed_instance": self.instance,
            "archive_sha256": self.archive,
            "wireguard_public_key": self.public,
            "now": self.now,
            "lifetime": 300,
        }
        values.update(changes)
        return module.prepare_session(**values)

    def test_valid_session(self):
        directory = self.prepare()

        challenge = json.loads(
            (directory / "challenge.json").read_text()
        )
        session = json.loads(
            (directory / "session.json").read_text()
        )

        self.assertEqual(
            challenge["instance_id"], self.instance
        )
        self.assertEqual(
            challenge["nonce"], session["nonce"]
        )
        self.assertEqual(
            challenge["expires"], self.now + 300
        )
        self.assertFalse(session["used"])

        self.assertEqual(
            stat.S_IMODE(directory.stat().st_mode), 0o700
        )

        for name in (
            "challenge.json",
            "session.json",
            "receiver-rsa-private.pem",
        ):
            self.assertEqual(
                stat.S_IMODE((directory / name).stat().st_mode),
                0o600,
            )

        private = serialization.load_pem_private_key(
            (directory / "receiver-rsa-private.pem").read_bytes(),
            password=None,
        )
        public = serialization.load_pem_public_key(
            challenge["public_pem"].encode("ascii")
        )
        self.assertEqual(private.key_size, 3072)
        self.assertEqual(
            private.public_key().public_numbers(),
            public.public_numbers(),
        )
        self.assertNotIn(
            "PRIVATE KEY", json.dumps(challenge)
        )

    def test_wrong_instance(self):
        with self.assertRaises(Exception):
            self.prepare(
                observed_instance="i-fffffffffffffffff"
            )
        self.assertFalse(
            (self.parent / "session").exists()
        )

    def test_invalid_lifetime(self):
        for value in (0, 601, True):
            with self.subTest(lifetime=value):
                with self.assertRaises(Exception):
                    self.prepare(lifetime=value)
                self.assertFalse(
                    (self.parent / "session").exists()
                )

    def test_invalid_public_key(self):
        with self.assertRaises(Exception):
            self.prepare(
                wireguard_public_key="invalid!!!"
            )
        self.assertFalse(
            (self.parent / "session").exists()
        )

    def test_duplicate_session(self):
        directory = self.prepare()

        original = {
            name: hashlib.sha256(
                (directory / name).read_bytes()
            ).hexdigest()
            for name in (
                "challenge.json",
                "session.json",
                "receiver-rsa-private.pem",
            )
        }

        with self.assertRaises(Exception):
            self.prepare()

        for name, digest in original.items():
            self.assertEqual(
                hashlib.sha256(
                    (directory / name).read_bytes()
                ).hexdigest(),
                digest,
            )

    def test_symlink_parent(self):
        alias = self.parent / "alias"
        alias.symlink_to(
            self.parent,
            target_is_directory=True,
        )

        with self.assertRaises(Exception):
            self.prepare(parent=alias)

        self.assertFalse(
            (self.parent / "session").exists()
        )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        SessionTests
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    if not result.wasSuccessful():
        print("RESULT=STOP")
        sys.exit(1)

    print("RESULT=SESSION_PREPARE_TEST_PASS")
