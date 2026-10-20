# 작성자: 이유빈
# 작성일: 2026-10-20
# 작성 내용: 기존 AWS 암호문을 메모리에서 복구해 승인한 세션용으로 암호화

import hashlib, json, os, stat, subprocess, sys, time, types
from pathlib import Path

def read(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError()
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size > 200000
    ):
        raise ValueError()
    return path.read_bytes()

def main():
    if len(sys.argv) != 4 or sys.argv[1] != '--apply':
        print('준비 상태: 운영 키 복구·암호화 미실행')
        return

    try:
        base = Path(__file__).resolve().parent
        binding = Path(sys.argv[2])
        approved = json.loads(read(binding))

        if approved.get('instance_id') != sys.argv[3]:
            raise ValueError()

        raw = read(base / 'vpn_aws_key_envelope.py')
        if hashlib.sha256(raw).hexdigest() != (
            'eb99d68acb91fd5440d86bcdfa0c5e3ee25975a71bc5cdf21c12168fdd609a62'
        ):
            raise ValueError()

        module = types.ModuleType('verified_envelope')
        exec(
            compile(raw, 'vpn_aws_key_envelope.py', 'exec'),
            module.__dict__,
        )

        challenge = json.loads(sys.stdin.read())
        archive = (
            Path.home()
            / '.local/share/seokpan-hybrid/secrets/vpn'
            / 'aws-gateway-wireguard.sops.json'
        )
        cipher = read(archive)
        digest = hashlib.sha256(cipher).hexdigest()

        if digest != (
            '65c6c09946f186fd5cf50169f2c330b4f9f5ac3dda58e29345cb45decbebf886'
        ):
            raise ValueError()

        identity = Path.home() / '.config/sops/age/keys.txt'
        if any(
            p.is_symlink()
            for p in (identity, *identity.parents)
        ):
            raise ValueError()

        info = identity.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
        ):
            raise ValueError()

        env = os.environ.copy()
        env.pop('SOPS_AGE_KEY', None)
        env.pop('SOPS_AGE_KEY_CMD', None)
        env['SOPS_AGE_KEY_FILE'] = str(identity)

        restored = subprocess.run(
            [
                '/usr/local/bin/sops',
                '--decrypt',
                '--input-type', 'json',
                '--output-type', 'json',
                '/dev/stdin',
            ],
            input=cipher,
            capture_output=True,
            env=env,
            timeout=60,
        )
        if restored.returncode != 0:
            raise ValueError()

        payload = json.loads(restored.stdout)
        if payload.get('wireguard_public_key') != (
            '/ROoAYvAbe6M3sp1h0UDgXS9DFo+1bVXrUE1/1ZCiAU='
        ):
            raise ValueError()

        envelope = module.seal(
            payload,
            challenge,
            approved['instance_id'],
            digest,
            int(time.time()),
        )
        print(json.dumps(envelope, separators=(',', ':')))

    except Exception:
        print(
            '중단: 승인 대상·암호문·복구·암호화 조건을 확인하세요. '
            '비밀값은 출력하지 않습니다.'
        )
        raise SystemExit(2)

if __name__ == '__main__':
    main()
