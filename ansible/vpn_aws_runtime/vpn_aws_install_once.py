"""신뢰한 수신 세션에서 한 번만 키 설치를 시도하는 연결 함수.
실제 서버 진입점은 별도다. 모듈을 읽는 것만으로 설치하지 않는다.
session.json은 전달문과 별도로 서버가 보존한 승인 정보여야 한다.
"""
import hashlib
import json
import os
import stat
import types
from pathlib import Path


def require(value):
    if not value:
        raise ValueError("수신 세션 또는 파일 보호 조건이 다릅니다.")


def secure(path, directory=False):
    path = Path(path)
    require(path.is_absolute())
    require(not any(p.is_symlink() for p in (path, *path.parents)))
    info = path.stat()
    require(info.st_uid == os.geteuid())
    require(
        stat.S_ISDIR(info.st_mode)
        if directory else stat.S_ISREG(info.st_mode)
    )
    require(
        stat.S_IMODE(info.st_mode)
        == (0o700 if directory else 0o600)
    )
    if not directory:
        require(info.st_nlink == 1 and info.st_size <= 8192)
    return path


def record(directory, name, value):
    # 새 파일만 만든다. 이미 있는 기록은 변경하지 않는다.
    path = directory / name
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())

    fd = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_receiver():
    path = Path(__file__).resolve().parent / "vpn_aws_key_receiver.py"
    require(not path.is_symlink())
    raw = path.read_bytes()
    require(
        hashlib.sha256(raw).hexdigest()
        == "b5307b9aabd9f60f7e6e3b8bf2cdde462fdc1d7a0d331f023f8887256e85774e"
    )
    module = types.ModuleType("verified_receiver")
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def install_once(
    directory, envelope, now, decrypt,
    derive_public, key_directory, expected
):
    require(os.name == "posix")
    directory = secure(directory, directory=True)
    require(not os.path.lexists(directory / "install-started.json"))

    session_path = secure(directory / "session.json")
    session = json.loads(session_path.read_text(encoding="utf-8"))

    require(
        set(expected)
        == {"instance_id", "archive_sha256", "wireguard_public_key"}
    )
    require(all(session.get(k) == v for k, v in expected.items()))

    receiver = load_receiver()
    payload = receiver.receive(
        envelope, session, now, decrypt, derive_public
    )

    # 설치 전에 기록을 저장·동기화한다.
    # 설치 실패·중단 뒤에도 표시를 지우지 않는다.
    # 동시 호출 중 한 호출만 이 파일을 새로 만들 수 있다.
    record(
        directory,
        "install-started.json",
        {"설치 시도 시작": True},
    )

    try:
        result = receiver.install_new_key(
            payload, key_directory, os.geteuid(), derive_public
        )
        record(
            directory,
            "install-completed.json",
            {"신규 키 설치": True},
        )
        return result
    except BaseException:
        raise RuntimeError(
            "설치 결과 확인이 필요합니다. "
            "시작 기록을 지우거나 자동 재실행하지 마세요."
        ) from None
