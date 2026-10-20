# 작성자: 이유빈
# 작성일: 2026-10-20
# 작성 내용: 서버 신원을 다시 확인하고 기존 일회 설치 함수 호출

import base64, hashlib, json, os, re, stat, sys, time, types
from pathlib import Path

def read(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError()

    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size > 200000
    ):
        raise ValueError()

    return path.read_bytes()

def main():
    if len(sys.argv) != 3 or sys.argv[1] != '--apply':
        print('준비 상태: 서버 키 설치 미실행')
        return

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa, x25519

        if os.geteuid() != 0:
            raise ValueError()

        directory = Path(sys.argv[2])
        if (
            not re.fullmatch(
                r'/run/seokpan-vpn-receiver-[A-Za-z0-9_-]+/session',
                str(directory),
            )
            or any(
                p.is_symlink()
                for p in (directory, *directory.parents)
            )
        ):
            raise ValueError()

        for path in (directory.parent, directory):
            info = path.stat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise ValueError()

        request = json.loads(sys.stdin.read())
        base = Path(__file__).resolve().parent
        modules = {}

        for name, digest in {'vpn_aws_install_once.py': 'a5b7757783e05c3a450b6880ef50c7a3132294dcc007d975739fc42b68a4af81', 'vpn_aws_key_receiver.py': 'b5307b9aabd9f60f7e6e3b8bf2cdde462fdc1d7a0d331f023f8887256e85774e', 'vpn_aws_imds_bridge.py': '4a49314f42dee2d8b9c8d1fa7cd88b7c9c9b810df962ecdebd4e16f5f4108ac3'}.items():
            raw = read(base / name)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError()

            module = types.ModuleType(name[:-3])
            module.__file__ = str(base / name)
            exec(
                compile(raw, str(base / name), 'exec'),
                module.__dict__,
            )
            modules[name] = module

        bridge = modules['vpn_aws_imds_bridge.py']
        bridge.verify_identity(
            request['approved'],
            bridge.read_imds_identity(),
        )

        private = serialization.load_pem_private_key(
            read(directory / 'receiver-rsa-private.pem'),
            password=None,
        )
        if (
            not isinstance(private, rsa.RSAPrivateKey)
            or private.key_size != 3072
        ):
            raise ValueError()

        def decrypt(cipher, label):
            return private.decrypt(
                cipher,
                padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=label,
                ),
            )

        def derive(value):
            raw = base64.b64decode(value, validate=True)
            key = x25519.X25519PrivateKey.from_private_bytes(raw)
            return base64.b64encode(
                key.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
            ).decode('ascii')

        expected = {
            'instance_id': request['approved']['instance_id'],
            'archive_sha256':
                '65c6c09946f186fd5cf50169f2c330b4f9f5ac3dda58e29345cb45decbebf886',
            'wireguard_public_key':
                '/ROoAYvAbe6M3sp1h0UDgXS9DFo+1bVXrUE1/1ZCiAU=',
        }

        for path in (
            Path('/etc/wireguard'),
            Path('/etc/wireguard/keys'),
        ):
            if any(
                p.is_symlink()
                for p in (path, *path.parents)
            ):
                raise ValueError()

            if not path.exists():
                path.mkdir(mode=0o700)

            info = path.stat()
            forbidden = 0o077 if path.name == 'keys' else 0o022

            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) & forbidden
            ):
                raise ValueError()

        result = modules['vpn_aws_install_once.py'].install_once(
            directory=directory,
            envelope=request['envelope'],
            now=int(time.time()),
            decrypt=decrypt,
            derive_public=derive,
            key_directory=Path('/etc/wireguard/keys'),
            expected=expected,
        )
        print(json.dumps(result, ensure_ascii=False))

    except Exception:
        print(
            '중단: 설치 결과가 불확실할 수 있습니다. '
            '기존 키·설치 기록을 삭제하거나 자동 재실행하지 마세요.'
        )
        raise SystemExit(2)

if __name__ == '__main__':
    main()
