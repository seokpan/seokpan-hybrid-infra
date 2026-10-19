
# 가짜 키·임시 폴더만 사용하는 새 공급 코드의 검사.
# 실제 서버와 키는 사용하지 않는다.

import base64
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "key_supply_remote",
    Path(__file__).with_name("vpn_key_install_remote.py"),
)
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


def example():
    return {
        "purpose": "onprem-gateway-wireguard",
        "gateway": "vpn-gw-01",
        "wireguard_private_key":
            base64.b64encode(bytes(range(32))).decode(),
        "wireguard_public_key":
            base64.b64encode(bytes(range(1, 33))).decode(),
    }


class PayloadChecks(unittest.TestCase):
    def test_valid_example(self):
        self.assertEqual(
            remote.validate_payload(example())[1],
            example()["wireguard_public_key"]
        )

    def test_wrong_destination(self):
        value = example()
        value["gateway"] = "different-server"
        with self.assertRaises(remote.SupplyError):
            remote.validate_payload(value)

    def test_bad_private_format(self):
        value = example()
        value["wireguard_private_key"] = "검사용잘못된값"
        with self.assertRaises(remote.SupplyError):
            remote.validate_payload(value)

    def test_short_public_key(self):
        value = example()
        value["wireguard_public_key"] = base64.b64encode(bytes(31)).decode()
        with self.assertRaises(remote.SupplyError):
            remote.validate_payload(value)


@unittest.skipUnless(
    os.name == "posix",
    "파일 보호 검사는 Controller의 Linux에서 수행"
)
class FileChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vpn-key-supply-test-"
        )
        self.parent = Path(self.temporary.name)
        self.directory = self.parent / "keys"
        self.target = self.directory / "wg0.key"
        self.payload = example()
        self.derive = Mock(
            return_value=self.payload["wireguard_public_key"]
        )

    def tearDown(self):
        self.temporary.cleanup()

    def install(self):
        return remote.install_key(
            self.payload, self.directory, os.getuid(), self.derive
        )

    def test_success_mode_and_content(self):
        report = self.install()
        self.assertEqual(
            self.target.read_text(),
            self.payload["wireguard_private_key"] + "\n"
        )
        self.assertEqual(
            stat.S_IMODE(self.target.stat().st_mode), 0o600
        )
        self.assertEqual(self.target.stat().st_uid, os.getuid())
        self.assertFalse(report["기존 파일 덮어쓰기"])

    def test_existing_file_preserved(self):
        self.directory.mkdir(mode=0o700)
        self.target.write_text("기존시험파일")
        with self.assertRaises(remote.SupplyError):
            self.install()
        self.assertEqual(self.target.read_text(), "기존시험파일")
        self.derive.assert_not_called()

    def test_mismatched_pair_installs_nothing(self):
        self.derive.return_value = "다른공개키"
        with self.assertRaises(remote.SupplyError):
            self.install()
        self.assertFalse(self.target.exists())

    def test_symlink_preserved(self):
        self.directory.mkdir(mode=0o700)
        other = self.parent / "기존자료"
        other.write_text("보존")
        self.target.symlink_to(other)
        with self.assertRaises(remote.SupplyError):
            self.install()
        self.assertEqual(other.read_text(), "보존")
        self.assertTrue(self.target.is_symlink())

    def test_write_failure_removes_new_file(self):
        with patch.object(
            remote.os, "fsync",
            side_effect=OSError("의도한 시험 실패")
        ):
            with self.assertRaises(OSError):
                self.install()
        self.assertFalse(self.target.exists())

    def test_unsafe_existing_directory_not_changed(self):
        self.directory.mkdir(mode=0o700)
        self.directory.chmod(0o755)
        with self.assertRaises(remote.SupplyError):
            self.install()
        self.assertEqual(
            stat.S_IMODE(self.directory.stat().st_mode), 0o755
        )
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    if os.name == "posix" and os.geteuid() == 0:
        raise SystemExit("Controller의 기존 ansible 계정으로 검사하세요.")

    suite = unittest.defaultTestLoader.loadTestsFromModule(
        __import__(__name__)
    )
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)

    labels = {
        "test_valid_example": "정상 시험 자료 형식",
        "test_wrong_destination": "다른 대상 서버 차단",
        "test_bad_private_format": "잘못된 개인키 형식 차단",
        "test_short_public_key": "잘못된 공개키 길이 차단",
        "test_success_mode_and_content": "정상 공급의 내용·소유자·권한",
        "test_existing_file_preserved": "기존 파일 보존",
        "test_mismatched_pair_installs_nothing": "키 짝 불일치 시 공급 차단",
        "test_symlink_preserved": "연결 파일 차단·기존 자료 보존",
        "test_write_failure_removes_new_file": "기록 실패 시 신규 파일 정리",
        "test_unsafe_existing_directory_not_changed": "기존 폴더 권한 보존·공급 차단",
    }

    print(json.dumps({
        "검사 수": result.testsRun,
        "실패 수": len(result.failures),
        "오류 수": len(result.errors),
        "건너뛴 수": len(result.skipped),
        "검사 성공": result.wasSuccessful(),
        "실패한 검사": [
            labels.get(
                test.id().split(".")[-1], "검사 이름 확인 필요"
            )
            for test, details in result.failures + result.errors
        ],
        "범위": "가짜 키·임시 폴더만 사용. 실제 복호화·서버 접속·키 공급 없음",
    }, ensure_ascii=False))

    raise SystemExit(0 if result.wasSuccessful() else 1)
