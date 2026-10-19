
# AWS 전용 키 생성·SOPS 보관 후보. 기본 실행은 안내만 출력한다.
import sys
sys.dont_write_bytecode = True
import argparse
import base64
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

RECIPIENTS = (
    "age1236mx0a84t6qq6shyqdtg06g60kqx2exshh7z8wkm366n2xf894q2mmtmf",
    "age159cw58jmknv4qmpsq8dx7k3jqu0gt7dggk6ltthmgjc65csfdyas6w8ark",
)

def require(condition, message):
    if not condition:
        raise ValueError(message)

def public_key(private):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    raw = base64.b64decode(private, validate=True)
    require(len(raw) == 32, "개인키 형식이 다릅니다.")
    key = X25519PrivateKey.from_private_bytes(raw)
    return base64.b64encode(key.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)).decode("ascii")

def generate_pair():
    raw = bytearray(os.urandom(32))
    raw[0] &= 248
    raw[31] = (raw[31] & 127) | 64
    private = base64.b64encode(raw).decode("ascii")
    return private, public_key(private)

def encrypt_and_verify(recipients, identity, env, runner=subprocess.run,
                       factory=generate_pair):
    require(len(recipients) == 2 and set(recipients) == set(RECIPIENTS),
            "승인한 두 보관자의 공개 수신자와 다릅니다.")
    private, public = factory()
    require(public_key(private) == public, "생성한 키의 대응이 다릅니다.")
    payload = {"purpose": "aws-gateway-wireguard",
               "gateway": "aws-vpn-gateway",
               "wireguard_private_key": private,
               "wireguard_public_key": public}
    clean = env.copy()
    clean.pop("SOPS_AGE_KEY", None)
    clean.pop("SOPS_AGE_KEY_CMD", None)
    clean["SOPS_AGE_KEY_FILE"] = str(identity)
    common = ["--input-type", "json", "--output-type", "json", "/dev/stdin"]
    encrypted = runner(
        ["/usr/local/bin/sops", "--encrypt", "--age", ",".join(recipients), *common],
        input=json.dumps(payload).encode(), env=clean,
        capture_output=True, timeout=60)
    require(encrypted.returncode == 0, "암호화하지 못했습니다.")
    cipher = encrypted.stdout
    require(isinstance(cipher, bytes) and len(cipher) <= 200000,
            "암호문 형식·크기가 다릅니다.")
    document = json.loads(cipher)
    require(document.get("wireguard_private_key", "").startswith("ENC["),
            "개인키 암호화 표시가 없습니다.")
    require(private.encode() not in cipher, "암호문 출력에 평문이 포함됐습니다.")
    actual = [item["recipient"] for item in document["sops"]["age"]]
    require(len(actual) == 2 and set(actual) == set(recipients),
            "암호문의 공개 수신자가 다릅니다.")
    restored = runner(
        ["/usr/local/bin/sops", "--decrypt", *common],
        input=cipher, env=clean, capture_output=True, timeout=60)
    require(restored.returncode == 0, "주 보관자의 복구 확인 실패")
    recovered = json.loads(restored.stdout)
    require(recovered == payload, "복구 자료가 생성 자료와 다릅니다.")
    require(public_key(recovered["wireguard_private_key"]) == public,
            "복구한 키의 대응이 다릅니다.")
    return cipher.rstrip() + b"\n", public

def write_new_archive(target, cipher):
    target = Path(target)
    require(not any(p.is_symlink() for p in (target, *target.parents)),
            "연결 파일 경로입니다.")
    require(not os.path.lexists(target), "기존 AWS 키 보관본이 있습니다.")
    require(os.name == "posix", "Linux 실행이 필요합니다.")
    info = target.parent.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and not stat.S_IMODE(info.st_mode) & 0o077,
            "보관 폴더 소유자·권한이 다릅니다.")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(cipher)
            stream.flush()
            os.fsync(stream.fileno())
        info = os.fstat(fd)
        require(info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_size == len(cipher), "보관 파일 보호 확인 실패")
    finally:
        os.close(fd)
    # 실패·취소 시 파일이 남을 수 있다. 기존·부분 파일은 자동 삭제하지 않는다.

def main():
    parser = argparse.ArgumentParser(description="AWS 키 암호화 보관 후보")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--expected-source")
    parser.add_argument("--recipients-file")
    args = parser.parse_args()
    if not args.create:
        print("준비 상태: AWS 키 생성·암호화·서버 공급 미실행")
        return 0
    stage = "실행 코드 확인"
    try:
        script = Path(__file__).absolute()
        require(not any(p.is_symlink() for p in (script, *script.parents)),
                "연결 파일 경로입니다.")
        script = script.resolve(strict=True)
        require(script.name == "vpn_aws_key_archive.py" and script.parent.name == "scripts",
                "실행 파일 위치가 다릅니다.")
        import vpn_onprem_key_supply as guard
        require(Path(guard.__file__).resolve()
                == script.with_name("vpn_onprem_key_supply.py"),
                "검사 모듈 위치가 다릅니다.")
        repo, env = guard.verify_source(
            script.with_name("vpn_onprem_key_supply.py"), args.expected_source)
        require(repo == script.parents[1], "검사 저장소와 실행 위치가 다릅니다.")
        stage = "보호 입력 확인"
        identity = Path.home() / ".config/sops/age/keys.txt"
        recipients_file = Path(args.recipients_file)
        guard.protected_file(identity)
        guard.protected_file(recipients_file)
        recipients = json.loads(recipients_file.read_bytes())["age_recipients"]
        target = (Path.home() / ".local/share/seokpan-hybrid/secrets/vpn"
                  / "aws-gateway-wireguard.sops.json")
        require(not os.path.lexists(target), "기존 AWS 키 보관본이 있습니다.")
        stage = "AWS 키 생성·암호화·주 보관자 복구 확인"
        cipher, public = encrypt_and_verify(recipients, identity, env)
        stage = "암호문 신규 저장"
        write_new_archive(target, cipher)
        print(json.dumps({
            "결과": "AWS 키 암호화 보관·주 보관자 복구 확인 완료",
            "암호문": str(target),
            "암호문 확인값": hashlib.sha256(cipher).hexdigest(),
            "AWS 공개키": public,
            "서버 공급": "미실행",
            "예비 보관자·독립 사본 복구": "미검증"
        }, ensure_ascii=False, indent=2))
        return 0
    except (Exception, KeyboardInterrupt):
        print("중단 단계:", stage)
        print("비밀값·원본 오류는 출력하지 않습니다. 서버·AWS 작업 없음.")
        print("새 암호문이 남았을 수 있습니다. 자동 재생성·삭제하지 마세요.")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
