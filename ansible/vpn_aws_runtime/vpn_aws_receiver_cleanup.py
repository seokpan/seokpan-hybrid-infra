# 작성자: 이유빈
# 작성일: 2026-10-20
# 작성 내용: 지정한 임시 수신 세션의 RSA 개인키만 정리

import os, re, stat, sys
from pathlib import Path

def main():
    if len(sys.argv) != 3 or sys.argv[1] != '--apply':
        print('준비 상태: 임시 수신키 정리 미실행')
        return

    try:
        directory = Path(sys.argv[2])

        if (
            os.geteuid() != 0
            or not re.fullmatch(
                r'/run/seokpan-vpn-receiver-[A-Za-z0-9_-]+/session',
                str(directory),
            )
        ):
            raise ValueError()

        if any(
            p.is_symlink()
            for p in (directory, *directory.parents)
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

        target = directory / 'receiver-rsa-private.pem'

        if os.path.lexists(target):
            info = target.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise ValueError()

            target.unlink()
            fd = os.open(
                directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

        print(
            '임시 RSA 수신키 없음 확인. '
            '운영 VPN 키와 설치 기록은 유지합니다.'
        )

    except Exception:
        print(
            '중단: 임시 수신키 정리 상태를 확인해야 합니다. '
            '강제 삭제하지 마세요.'
        )
        raise SystemExit(2)

if __name__ == '__main__':
    main()
