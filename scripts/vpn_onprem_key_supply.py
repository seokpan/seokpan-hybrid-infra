
# Controller에서 실행할 키 복원·공급 후보.
# 기본 실행은 안내만 출력한다.

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path

ARCHIVE_HASH = "8e45de907e5e4e5c9850c5c9d3ac9c3f8621494c0d9367eae1651460fd2beef6"


def protected_file(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("연결 파일 경로는 사용하지 않습니다.")

    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("보호 파일의 형식·소유자·권한이 다릅니다.")

    if info.st_size > 200000:
        raise ValueError("보호 파일 크기 제한 초과")


def verify_source(script_file, expected_source):
    script = Path(script_file).absolute()
    if any(part.is_symlink() for part in (script, *script.parents)):
        raise ValueError("연결 파일 경로의 실행 코드는 사용하지 않습니다.")
    script = script.resolve(strict=True)
    if script.name != "vpn_onprem_key_supply.py" or script.parent.name != "scripts":
        raise ValueError("실행 파일 위치가 예상 구조와 다릅니다.")
    repo = script.parents[1]
    env = {name: value for name, value in os.environ.items()
           if not name.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    commands = (
        (["rev-parse", "--show-toplevel"], None),
        (["rev-parse", "HEAD"], expected_source),
        (["branch", "--show-current"], "main"),
        (["status", "--porcelain", "--untracked-files=all"], ""),
    )
    for command, expected in commands:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *command],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            raise ValueError("실행 저장소를 확인하지 못했습니다.")
        actual = result.stdout.strip()
        if expected is None:
            if Path(actual).resolve() != repo:
                raise ValueError("실행 파일과 저장소 최상위 위치가 다릅니다.")
        elif actual != expected:
            raise ValueError("실행 Source·브랜치·작업 상태가 다릅니다.")
    return repo, env


def main():
    parser = argparse.ArgumentParser(
        description="기존 온프렘 키 공급 후보. 실제 실행은 별도 단계에서 수행"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="실제 복호화·서버 공급 실행"
    )
    parser.add_argument(
        "--expected-source", help="별도로 확인한 실행 기준 커밋"
    )
    args = parser.parse_args()

    if not args.apply:
        print(
            "준비 상태: 실제 공급 미실행."
            " 모의시험과 코드 리뷰 후 별도 실행 단계에서 사용합니다."
        )
        return 0

    try:
        if (
            os.name != "posix"
            or not re.fullmatch(
                r"[0-9a-f]{40}", args.expected_source or ""
            )
        ):
            raise ValueError("실행 환경 또는 기준 커밋 입력이 다릅니다.")

        home = Path.home()
        repo, env = verify_source(__file__, args.expected_source)
        from vpn_key_install_remote import validate_payload

        archive = (
            home / ".local/share/seokpan-hybrid/secrets/vpn"
            / "onprem-gateway-wireguard.sops.json"
        )
        identity = home / ".config/sops/age/keys.txt"

        protected_file(archive)
        protected_file(identity)

        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_HASH:
            raise ValueError("기존 암호문 확인값이 다릅니다.")

        env.pop("SOPS_AGE_KEY", None)
        env.pop("SOPS_AGE_KEY_CMD", None)
        env["SOPS_AGE_KEY_FILE"] = str(identity)

        result = subprocess.run(
            [
                "/usr/local/bin/sops", "--decrypt",
                "--input-type", "json", "--output-type", "json",
                str(archive)
            ],
            env=env,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise ValueError("기존 암호문 복호화 실패")

        payload = json.loads(result.stdout)
        private, public = validate_payload(payload)

        outgoing = json.dumps({
            "purpose": payload["purpose"],
            "gateway": payload["gateway"],
            "wireguard_private_key": private,
            "wireguard_public_key": public,
        }).encode()

        helper = (repo / "scripts/vpn_key_install_remote.py").read_text(
            encoding="utf-8"
        )

        print(
            "승인한 기존 키의 서버 공급을 실행합니다."
            " 키 값은 출력하지 않습니다.",
            flush=True
        )
        result = subprocess.run(
            [
                "ssh", "-T",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "UpdateHostKeys=no",
                "-o", "ForwardAgent=no",
                "-o", "ClearAllForwardings=yes",
                "-o", "PermitLocalCommand=no",
                "-o", "ControlPath=none",
                "-o", "ConnectTimeout=5",
                "-o", "NumberOfPasswordPrompts=1",
                "root@10.1.93.98",
                "python3 -B -c " + shlex.quote(helper),
            ],
            input=outgoing,
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise ValueError(
                "서버 공급 또는 결과 수신 실패. 부분 공급 여부 확인 필요"
            )

        report = json.loads(result.stdout)
        if report.get("결과") != "기존 키 복원·공급 완료":
            raise ValueError("공급 완료 응답을 확인하지 못했습니다.")

        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("별도 후속: 실제 기동·터널·통신·서버 키의 복구 검증")
        return 0

    except Exception:
        print(
            "중단: 실행 조건·복호화·공급 또는 결과 확인 실패."
            " 비밀값과 원본 오류는 출력하지 않습니다."
        )
        print(
            "부분 공급됐을 수 있으므로 자동 재실행·기존 키 삭제 없이"
            " 상태를 확인하세요."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
