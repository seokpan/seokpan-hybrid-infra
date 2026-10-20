
import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path

FIELDS = {
    "instance_id", "nonce", "expires",
    "archive_sha256", "wireguard_public_key"
}


def require(condition):
    if not condition:
        raise ValueError("수신 대상·세션·자료·파일 보호 조건이 다릅니다.")


def label(values):
    context = {name: values[name] for name in FIELDS}
    return hashlib.sha256(json.dumps(
        context, sort_keys=True, separators=(",", ":")
    ).encode()).digest()


def validate_payload(payload, derive_public):
    require(isinstance(payload, dict) and set(payload) == {
        "purpose", "gateway", "wireguard_private_key", "wireguard_public_key"
    })
    require(payload["purpose"] == "aws-gateway-wireguard")
    require(payload["gateway"] == "aws-vpn-gateway")
    for name in ("wireguard_private_key", "wireguard_public_key"):
        value = payload[name]
        require(isinstance(value, str))
        raw = base64.b64decode(value, validate=True)
        require(len(raw) == 32 and any(raw))
        require(base64.b64encode(raw).decode() == value)
    require(
        derive_public(payload["wireguard_private_key"])
        == payload["wireguard_public_key"]
    )


def receive(envelope, session, now, decrypt, derive_public):
    # session은 수신 서버가 별도로 보존한 승인 정보여야 한다.
    # 전달문에서 복사한 값을 승인 정보로 사용하면 안 된다.
    require(isinstance(session, dict) and set(session) == FIELDS | {"used"})
    require(isinstance(envelope, dict) and set(envelope) == FIELDS | {"ciphertext"})
    require(session["used"] is False)
    require(all(envelope[name] == session[name] for name in FIELDS))
    require(
        isinstance(session["instance_id"], str)
        and re.fullmatch(
            r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", session["instance_id"]
        )
    )
    for name in ("nonce", "archive_sha256"):
        require(
            isinstance(session[name], str)
            and re.fullmatch(r"[0-9a-f]{64}", session[name])
        )
    require(type(now) is int and type(session["expires"]) is int)
    require(now < session["expires"] <= now + 600)

    encoded = envelope["ciphertext"]
    require(isinstance(encoded, str) and len(encoded) == 512)
    cipher = base64.b64decode(encoded, validate=True)
    require(len(cipher) == 384)

    raw = decrypt(cipher, label(session))
    require(isinstance(raw, bytes) and len(raw) <= 318)
    payload = json.loads(raw)
    validate_payload(payload, derive_public)
    require(payload["wireguard_public_key"] == session["wireguard_public_key"])
    session["used"] = True

    # 실제 구현에는 사용 상태 영구 기록·중복 실행 차단이 추가로 필요하다.
    return payload


def install_new_key(payload, directory, owner, derive_public):
    require(os.name == "posix")
    validate_payload(payload, derive_public)
    directory = Path(directory)
    target = directory / "wg0.key"

    require(not any(p.is_symlink() for p in (target, *target.parents)))
    require(not os.path.lexists(target))

    # 폴더 준비는 별도 단계다. 기존 폴더 권한을 바꾸지 않는다.
    for path in (directory.parent, directory):
        info = path.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner)
        forbidden = 0o077 if path == directory else 0o022
        require(not stat.S_IMODE(info.st_mode) & forbidden)

    created = None
    try:
        fd = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            created = os.fstat(fd)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(
                    (payload["wireguard_private_key"] + "\n").encode("ascii")
                )
                stream.flush()
                os.fsync(fd)
            info = os.fstat(fd)
            require(
                info.st_uid == owner
                and stat.S_IMODE(info.st_mode) == 0o600
            )
            require(info.st_size == 45)
        finally:
            os.close(fd)
    except Exception:
        if created is not None:
            current = target.lstat()
            require(
                (current.st_dev, current.st_ino)
                == (created.st_dev, created.st_ino)
            )
            target.unlink()
        raise

    return {
        "신규 키 설치": True, "권한": "600",
        "덮어쓰기": False, "서비스 시작": False,
    }


if __name__ == "__main__":
    print("준비 후보: 실제 수신·서버 설치·AWS 실행 없음")
