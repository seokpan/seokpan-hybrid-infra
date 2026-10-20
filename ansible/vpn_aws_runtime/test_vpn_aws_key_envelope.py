
import base64
import io
import json
import sys
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, x25519
import vpn_aws_key_envelope as module


class EnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 시험용 배송 열쇠다. 실제 WireGuard 키가 아니다.
        cls.receiver = rsa.generate_private_key(
            public_exponent=65537, key_size=3072
        )

        raw = bytearray(b"A" * 32)
        raw[0] &= 248
        raw[31] = (raw[31] & 127) | 64

        public = x25519.X25519PrivateKey.from_private_bytes(
            bytes(raw)
        ).public_key()

        cls.payload = {
            "purpose": "aws-gateway-wireguard",
            "gateway": "aws-vpn-gateway",
            "wireguard_private_key": base64.b64encode(raw).decode(),
            "wireguard_public_key": base64.b64encode(
                public.public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
            ).decode(),
        }
        cls.challenge = {
            "instance_id": "i-0123456789abcdef0",
            "nonce": "a" * 64,
            "expires": 1100,
            "public_pem": cls.receiver.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode(),
        }

    def make(self, payload=None, challenge=None, digest="b" * 64):
        return module.seal(
            self.payload if payload is None else payload,
            self.challenge if challenge is None else challenge,
            "i-0123456789abcdef0", digest, 1000,
        )

    def decrypt(self, envelope):
        return self.receiver.decrypt(
            base64.b64decode(envelope["ciphertext"]),
            padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=module.context_label(envelope),
            ),
        )

    def test_roundtrip(self):
        self.assertEqual(json.loads(self.decrypt(self.make())), self.payload)

    def test_private_absent_from_envelope(self):
        self.assertNotIn(
            self.payload["wireguard_private_key"], json.dumps(self.make())
        )

    def test_wrong_instance(self):
        value = dict(self.challenge, instance_id="i-fffffffffffffffff")
        with self.assertRaises(ValueError):
            self.make(challenge=value)

    def test_expired(self):
        with self.assertRaises(ValueError):
            self.make(challenge=dict(self.challenge, expires=1000))

    def test_excessive_lifetime(self):
        with self.assertRaises(ValueError):
            self.make(challenge=dict(self.challenge, expires=1601))

    def test_bad_nonce(self):
        with self.assertRaises(ValueError):
            self.make(challenge=dict(self.challenge, nonce="wrong"))

    def test_wrong_wireguard_public(self):
        value = dict(
            self.payload,
            wireguard_public_key=base64.b64encode(b"B" * 32).decode(),
        )
        with self.assertRaises(ValueError):
            self.make(payload=value)

    def test_bad_archive_hash(self):
        with self.assertRaises(ValueError):
            self.make(digest="wrong")

    def test_changed_target_cannot_decrypt(self):
        value = self.make()
        value["instance_id"] = "i-fffffffffffffffff"
        with self.assertRaises(ValueError):
            self.decrypt(value)

    def test_damaged_ciphertext(self):
        value = self.make()
        raw = bytearray(base64.b64decode(value["ciphertext"]))
        raw[0] ^= 1
        value["ciphertext"] = base64.b64encode(raw).decode()
        with self.assertRaises(ValueError):
            self.decrypt(value)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EnvelopeTests)
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    print(json.dumps({
        "검사 수": result.testsRun,
        "실패 수": len(result.failures),
        "오류 수": len(result.errors),
        "건너뜀 수": len(result.skipped),
        "검사 성공": result.wasSuccessful(),
        "범위": "가짜 VPN 키·메모리의 시험용 RSA 키. 실제 키·SOPS·서버·AWS 사용 없음",
    }, ensure_ascii=False))
    sys.exit(0 if result.wasSuccessful() else 1)
