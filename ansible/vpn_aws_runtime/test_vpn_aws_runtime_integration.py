import base64
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, x25519

ROOT = Path(__file__).resolve().parent


def load_verified(name, module_name):
    import types
    manifest_path = ROOT / "candidate-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("후보 확인값 기록이 없습니다.")

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )
    expected = manifest["files"][name]
    path = ROOT / name

    if path.is_symlink() or not path.is_file():
        raise RuntimeError("검토 대상 후보 파일이 없습니다.")

    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RuntimeError("기록한 확인값과 코드가 다릅니다.")

    # 확인한 바이트를 직접 사용한다. 기존 캐시는 읽지 않는다.
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def derive_public(private_b64):
    raw = base64.b64decode(private_b64, validate=True)
    private = x25519.X25519PrivateKey.from_private_bytes(raw)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(public).decode("ascii")


class RuntimeIntegrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.envelope_module = load_verified(
            "vpn_aws_key_envelope.py", "offline_envelope"
        )
        cls.install_module = load_verified(
            "vpn_aws_install_once.py", "offline_install"
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="v04-offline-", dir=str(ROOT)
        )
        self.addCleanup(self.temp.cleanup)

        root = Path(self.temp.name)
        self.session_dir = root / "session"
        self.key_dir = root / "keys"

        self.session_dir.mkdir(mode=0o700)
        self.key_dir.mkdir(mode=0o700)

        self.now = 2000000000
        self.instance_id = "i-0123456789abcdef0"
        self.archive_hash = hashlib.sha256(
            b"offline-test-archive"
        ).hexdigest()

        wg_private = x25519.X25519PrivateKey.generate()
        private_raw = wg_private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )

        private_b64 = base64.b64encode(private_raw).decode("ascii")
        approved_public = derive_public(private_b64)

        self.payload = {
            "purpose": "aws-gateway-wireguard",
            "gateway": "aws-vpn-gateway",
            "wireguard_private_key": private_b64,
            "wireguard_public_key": approved_public,
        }

        self.rsa_private = rsa.generate_private_key(
            public_exponent=65537, key_size=3072
        )

        challenge = {
            "instance_id": self.instance_id,
            "nonce": hashlib.sha256(os.urandom(32)).hexdigest(),
            "expires": self.now + 120,
            "public_pem": self.rsa_private.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii"),
        }

        self.envelope = self.envelope_module.seal(
            self.payload,
            challenge,
            self.instance_id,
            self.archive_hash,
            self.now,
        )

        # 수신자가 독립적으로 신뢰하는 정보의 시험용 모형.
        # 실제 구현에서는 EC2 신원 및 승인된 공개키를
        # 별도의 신뢰 경로로 확인해야 한다.
        self.session = {
            "instance_id": challenge["instance_id"],
            "nonce": challenge["nonce"],
            "expires": challenge["expires"],
            "archive_sha256": self.archive_hash,
            "wireguard_public_key": approved_public,
            "used": False,
        }

        session_file = self.session_dir / "session.json"
        with session_file.open("x", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(self.session, stream)
            stream.flush()
            os.fsync(stream.fileno())

    def decrypt(self, cipher, label):
        return self.rsa_private.decrypt(
            cipher,
            padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=label,
            ),
        )

    def install(self, envelope=None, now=None, expected=None):
        if envelope is None:
            envelope = self.envelope
        if now is None:
            now = self.now
        if expected is None:
            expected = {
                "instance_id": self.instance_id,
                "archive_sha256": self.archive_hash,
                "wireguard_public_key":
                    self.session["wireguard_public_key"],
            }

        return self.install_module.install_once(
            self.session_dir,
            envelope,
            now,
            self.decrypt,
            derive_public,
            self.key_dir,
            expected,
        )

    def started(self):
        return (self.session_dir / "install-started.json").exists()

    def test_full_flow_and_retry_block(self):
        result = self.install()
        self.assertTrue(result["신규 키 설치"])

        key_path = self.key_dir / "wg0.key"
        self.assertTrue(key_path.is_file())
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)

        expected_content = (
            self.payload["wireguard_private_key"] + "\n"
        ).encode("ascii")
        self.assertEqual(key_path.read_bytes(), expected_content)

        with self.assertRaises(Exception):
            self.install()

        self.assertEqual(key_path.read_bytes(), expected_content)
        self.assertTrue(self.started())

    def test_wrong_instance_rejected(self):
        expected = {
            "instance_id": "i-fffffffffffffffff",
            "archive_sha256": self.archive_hash,
            "wireguard_public_key":
                self.session["wireguard_public_key"],
        }

        with self.assertRaises(Exception):
            self.install(expected=expected)

        self.assertFalse(self.started())
        self.assertFalse((self.key_dir / "wg0.key").exists())

    def test_expired_session_rejected(self):
        with self.assertRaises(Exception):
            self.install(now=self.now + 180)

        self.assertFalse(self.started())
        self.assertFalse((self.key_dir / "wg0.key").exists())

    def test_damaged_ciphertext_rejected(self):
        damaged = dict(self.envelope)
        original = damaged["ciphertext"]
        replacement = "A" if original[0] != "A" else "B"
        damaged["ciphertext"] = replacement + original[1:]

        with self.assertRaises(Exception):
            self.install(envelope=damaged)

        self.assertFalse(self.started())
        self.assertFalse((self.key_dir / "wg0.key").exists())

    def test_existing_key_preserved_and_retry_blocked(self):
        key_path = self.key_dir / "wg0.key"
        previous = b"existing-offline-test-key\n"

        with key_path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(previous)

        with self.assertRaises(Exception):
            self.install()

        self.assertTrue(self.started())
        self.assertFalse(
            (self.session_dir / "install-completed.json").exists()
        )
        self.assertEqual(key_path.read_bytes(), previous)

        with self.assertRaises(Exception):
            self.install()

        self.assertEqual(key_path.read_bytes(), previous)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        RuntimeIntegrationTests
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    if not result.wasSuccessful():
        print("RESULT=STOP")
        sys.exit(1)

    print("RESULT=V04_OFFLINE_INTEGRATION_PASS")
