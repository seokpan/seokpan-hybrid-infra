import base64
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(filename, name):
    path = ROOT / filename
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("module file rejected")

    spec = importlib.util.spec_from_file_location(
        name, path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load(
    "vpn_aws_imds_bridge.py", "offline_bridge"
)
session = load(
    "vpn_aws_session_prepare.py", "offline_session"
)


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count=-1):
        return self.data[:count]


class FakeOpener:
    def __init__(self, identity):
        self.identity = identity
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)

        if len(self.calls) == 1:
            if request.get_method() != "PUT":
                raise RuntimeError("token method mismatch")
            return Response(b"test-token")

        if request.get_header(
            "X-aws-ec2-metadata-token"
        ) != "test-token":
            raise RuntimeError("IMDSv2 token missing")

        return Response(
            json.dumps(self.identity).encode("ascii")
        )


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="v04-imds-test-",
            dir=str(ROOT),
        )
        self.addCleanup(self.temp.cleanup)

        self.parent = Path(self.temp.name)

        self.approved = {
            "account_id": "000000000000",
            "region": "ap-northeast-2",
            "instance_id": "i-0123456789abcdef0",
            "image_id": "ami-0123456789abcdef0",
            "private_ip": "192.168.64.10",
        }

        self.observed = {
            "accountId": "000000000000",
            "region": "ap-northeast-2",
            "instanceId": "i-0123456789abcdef0",
            "imageId": "ami-0123456789abcdef0",
            "privateIp": "192.168.64.10",
        }

        self.archive = hashlib.sha256(
            b"offline-only"
        ).hexdigest()

        self.public = base64.b64encode(
            bytes(range(1, 33))
        ).decode("ascii")

    def prepare(self):
        return bridge.prepare_receiver_session(
            parent=self.parent,
            approved=self.approved,
            archive_sha256=self.archive,
            wireguard_public_key=self.public,
            now=2000000000,
            prepare_function=session.prepare_session,
            identity_reader=lambda: self.observed,
        )

    def test_valid_identity_and_session(self):
        directory = self.prepare()
        self.assertTrue(
            (directory / "session.json").is_file()
        )

        saved = json.loads(
            (directory / "session.json").read_text()
        )
        self.assertEqual(
            saved["instance_id"],
            self.approved["instance_id"],
        )

    def test_wrong_account(self):
        self.observed["accountId"] = "111111111111"
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(
            (self.parent / "session").exists()
        )

    def test_wrong_instance(self):
        self.observed["instanceId"] = (
            "i-fffffffffffffffff"
        )
        with self.assertRaises(ValueError):
            self.prepare()

    def test_wrong_region(self):
        self.observed["region"] = "us-east-1"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_wrong_image(self):
        self.observed["imageId"] = (
            "ami-fffffffffffffffff"
        )
        with self.assertRaises(ValueError):
            self.prepare()

    def test_wrong_private_ip(self):
        self.observed["privateIp"] = "192.168.64.99"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_imdsv2_token_flow(self):
        opener = FakeOpener(self.observed)

        document = bridge.read_imds_identity(
            opener=opener
        )

        self.assertEqual(
            document["instanceId"],
            self.approved["instance_id"],
        )
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(
            opener.calls[0].get_method(),
            "PUT",
        )
        self.assertEqual(
            opener.calls[1].get_method(),
            "GET",
        )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        BridgeTests
    )
    result = unittest.TextTestRunner(
        verbosity=2
    ).run(suite)

    if not result.wasSuccessful():
        print("RESULT=STOP")
        sys.exit(1)

    print("RESULT=IMDS_SESSION_BRIDGE_PASS")
