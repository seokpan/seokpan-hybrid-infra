
# 온프렘 서버에서 호출할 키 공급 후보.
# 직접 실행은 별도 실제 공급 단계에서만 한다.

import base64
import binascii
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path


class SupplyError(Exception):
    pass


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise SupplyError("키 자료 형식이 다릅니다.")
    if (
        payload.get("purpose") != "onprem-gateway-wireguard"
        or payload.get("gateway") != "vpn-gw-01"
    ):
        raise SupplyError("보관 목적 또는 대상 서버가 다릅니다.")

    result = []
    for field in ("wireguard_private_key", "wireguard_public_key"):
        value = payload.get(field)
        if not isinstance(value, str) or value != value.strip():
            raise SupplyError("키 문자열 형식이 다릅니다.")
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise SupplyError("키 인코딩 형식이 다릅니다.") from None
        if (
            len(decoded) != 32
            or base64.b64encode(decoded).decode() != value
        ):
            raise SupplyError("키 길이 또는 표기 형식이 다릅니다.")
        result.append(value)
    return tuple(result)


def reject_symlinks(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise SupplyError("연결 파일 경로는 사용하지 않습니다.")


def install_key(payload, directory, expected_owner, derive_public):
    private, public = validate_payload(payload)
    directory = Path(directory)
    target = directory / "wg0.key"

    reject_symlinks(target)
    if os.path.lexists(target):
        raise SupplyError("기존 키 파일이 있습니다. 덮어쓰지 않습니다.")

    if derive_public(private) != public:
        raise SupplyError("복원한 개인키와 보관된 공개키의 짝이 다릅니다.")

    for path in (directory.parent, directory):
        reject_symlinks(path)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass

        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != expected_owner
        ):
            raise SupplyError("공급 폴더의 형식 또는 소유자가 다릅니다.")

        forbidden = 0o077 if path == directory else 0o022
        if stat.S_IMODE(info.st_mode) & forbidden:
            raise SupplyError(
                "기존 공급 폴더의 권한 검토가 필요합니다."
                " 자동 변경하지 않습니다."
            )

    created = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(target, flags, 0o600)
        try:
            created = os.fstat(descriptor)
            os.fchmod(descriptor, 0o600)

            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write((private + "\n").encode("ascii"))
                stream.flush()
                os.fsync(descriptor)

            info = os.fstat(descriptor)
            if (
                info.st_uid != expected_owner
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise SupplyError("공급 파일 소유자 또는 권한이 다릅니다.")
            if info.st_size != 45:
                raise SupplyError("공급한 키 파일 길이가 다릅니다.")
        finally:
            os.close(descriptor)

    except Exception:
        if created is not None:
            current = target.lstat()
            if (
                current.st_dev, current.st_ino
            ) != (
                created.st_dev, created.st_ino
            ):
                raise SupplyError(
                    "공급 중 대상이 바뀌었습니다. 자동 삭제하지 않습니다."
                ) from None
            target.unlink()
        raise

    return {
        "결과": "기존 키 복원·공급 완료",
        "키 짝 확인": True,
        "파일 소유자 번호": info.st_uid,
        "파일 권한": "600",
        "기존 파일 덮어쓰기": False,
        "서비스 시작": "하지 않음",
    }


def derive_public(private):
    result = subprocess.run(
        ["/usr/bin/wg", "pubkey"],
        input=private + "\n",
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise SupplyError("서버에서 공개키 대응을 계산하지 못했습니다.")
    return result.stdout.strip()


def main():
    try:
        if (
            os.name != "posix"
            or os.geteuid() != 0
            or socket.gethostname().split(".")[0] != "vpn-gw-01"
        ):
            raise SupplyError("승인한 서버의 root 실행이 아닙니다.")

        service = subprocess.run(
            ["systemctl", "is-active", "wg-quick@wg0.service"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if (
            service.returncode != 3
            or service.stdout.strip() != "inactive"
        ):
            raise SupplyError(
                "VPN 서비스가 비활성 상태인지 확인하지 못했습니다."
            )

        raw = sys.stdin.buffer.read(200001)
        if len(raw) > 200000:
            raise SupplyError("전달 자료 크기 제한 초과")

        payload = json.loads(raw)
        report = install_key(
            payload, "/etc/wireguard/keys", 0, derive_public
        )
        print(json.dumps(report, ensure_ascii=False))

    except Exception:
        print(json.dumps({
            "결과": "키 공급 실패·자동 재실행 금지",
            "안내": (
                "기존 파일·폴더·키 대응·서비스 상태를 검토하세요."
                " 비밀값은 출력하지 않습니다."
            ),
        }, ensure_ascii=False))
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
