
import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, x25519
import vpn_aws_key_envelope as sender
import vpn_aws_key_receiver as receiver
from test_vpn_aws_key_envelope import EnvelopeTests


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 가짜 자료 준비 함수만 사용한다. 기존 시험은 실행하지 않는다.
        EnvelopeTests.setUpClass()
        cls.payload = EnvelopeTests.payload
        cls.challenge = EnvelopeTests.challenge
        cls.private = EnvelopeTests.receiver

    def setUp(self):
        self.envelope = sender.seal(
            self.payload, self.challenge,
            "i-0123456789abcdef0", "b" * 64, 1000,
        )
        self.session = {
            name: self.envelope[name] for name in receiver.FIELDS
        }
        self.session["used"] = False

    @staticmethod
    def derive(value):
        public = x25519.X25519PrivateKey.from_private_bytes(
            base64.b64decode(value)
        ).public_key()
        return base64.b64encode(public.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )).decode()

    def decrypt(self, cipher, label):
        return self.private.decrypt(
            cipher,
            padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=label,
            ),
        )

    def receive(self, now=1000):
        return receiver.receive(
            self.envelope, self.session, now,
            self.decrypt, self.derive,
        )

    def test_receive_and_consume(self):
        self.assertEqual(self.receive(), self.payload)
        self.assertTrue(self.session["used"])

    def test_wrong_instance(self):
        self.envelope["instance_id"] = "i-fffffffffffffffff"
        with self.assertRaises(ValueError):
            self.receive()

    def test_wrong_session(self):
        self.envelope["nonce"] = "c" * 64
        with self.assertRaises(ValueError):
            self.receive()

    def test_expired(self):
        with self.assertRaises(ValueError):
            self.receive(now=1100)

    def test_replay(self):
        self.receive()
        with self.assertRaises(ValueError):
            self.receive()

    def test_wrong_archive_reference(self):
        self.envelope["archive_sha256"] = "c" * 64
        with self.assertRaises(ValueError):
            self.receive()

    def test_corrupt_cipher(self):
        raw = bytearray(base64.b64decode(self.envelope["ciphertext"]))
        raw[-1] ^= 1
        self.envelope["ciphertext"] = base64.b64encode(raw).decode()
        with self.assertRaises(ValueError):
            self.receive()

    def test_wrong_key_pair(self):
        with self.assertRaises(ValueError):
            receiver.receive(
                self.envelope, self.session, 1000, self.decrypt,
                lambda value: base64.b64encode(b"Z" * 32).decode(),
            )

    def folder(self, root):
        root = Path(root)
        root.chmod(0o700)
        folder = root / "keys"
        folder.mkdir(mode=0o700)
        return folder

    @unittest.skipUnless(os.name == "posix", "Linux 파일 보호 검사")
    def test_new_file(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.folder(root)
            receiver.install_new_key(
                self.payload, folder, os.getuid(), self.derive
            )
            target = folder / "wg0.key"
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.stat().st_uid, os.getuid())
            self.assertEqual(
                target.read_text().strip(),
                self.payload["wireguard_private_key"],
            )

    @unittest.skipUnless(os.name == "posix", "Linux 파일 보호 검사")
    def test_existing_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.folder(root)
            target = folder / "wg0.key"
            target.write_bytes(b"preserved")
            with self.assertRaises(ValueError):
                receiver.install_new_key(
                    self.payload, folder, os.getuid(), self.derive
                )
            self.assertEqual(target.read_bytes(), b"preserved")

    @unittest.skipUnless(os.name == "posix", "Linux 파일 보호 검사")
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.folder(root)
            other = Path(root) / "preserved"
            other.write_bytes(b"preserved")
            (folder / "wg0.key").symlink_to(other)
            with self.assertRaises(ValueError):
                receiver.install_new_key(
                    self.payload, folder, os.getuid(), self.derive
                )
            self.assertEqual(other.read_bytes(), b"preserved")

    @unittest.skipUnless(os.name == "posix", "Linux 파일 보호 검사")
    def test_failed_write_removes_only_new_file(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.folder(root)
            other = folder / "keep"
            other.write_bytes(b"preserved")
            with patch.object(
                receiver.os, "fsync", side_effect=OSError("test")
            ):
                with self.assertRaises(OSError):
                    receiver.install_new_key(
                        self.payload, folder, os.getuid(), self.derive
                    )
            self.assertFalse((folder / "wg0.key").exists())
            self.assertEqual(other.read_bytes(), b"preserved")


if __name__ == "__main__":
    # 새 ReceiverTests만 실행한다.
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReceiverTests)
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    print(json.dumps({
        "검사 수": result.testsRun,
        "실패 수": len(result.failures),
        "오류 수": len(result.errors),
        "건너뜀 수": len(result.skipped),
        "검사 성공": result.wasSuccessful(),
        "범위": "가짜 키·임시 폴더·새 수신/설치 함수만 검사",
    }, ensure_ascii=False))
    raise SystemExit(0 if result.wasSuccessful() else 1)
