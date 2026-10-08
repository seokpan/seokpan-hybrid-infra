# Data 백업 · 복원 스크립트

온프렘 15분 백업(RDS → age 암호화 사본 → Backup S3 · 복구 DB VM)과 장애 시 복원(controller → 복구 DB VM)에 쓰는 스크립트입니다. 이 폴더가 기준본이며, 설치된 파일은 아래 해시와 같아야 합니다.

## 파일과 설치 위치

| 저장소 파일 | 설치 위치 | 설치 권한 | SHA-256 앞 12자리 |
|---|---|---|---|
| `backup-vm/seokpan-hybrid-backup` | hybrid-backup-01 `/usr/local/sbin/` | 700 root:root | `34bbf46333a8` |
| `backup-vm/seokpan-hybrid-backup.service` | hybrid-backup-01 `/etc/systemd/system/` | 644 root:root | `c5d4df879a19` |
| `backup-vm/seokpan-hybrid-backup.timer` | hybrid-backup-01 `/etc/systemd/system/` | 644 root:root | `ed1afefbbc1a` |
| `backup-vm/backup.env.example` | 견본 → hybrid-backup-01 `/etc/seokpan-hybrid-backup/backup.env` | 600 root:root | (견본, 값은 환경별) |
| `recovery-db-vm/seokpan-recv-copy` | hybrid-recovery-db-01 `/usr/local/sbin/` | 755 root:root | `f400647f9ff8` |
| `controller/restore.sh` | controller(ksh) `~/recovery/` | 700 ksh:ksh | `73c0956570ae` |
| `controller/table_hashes.sh` | controller(ksh) `~/recovery/` | 700 ksh:ksh | `f37301c35d45` |

Git은 실행 권한을 755 · 644로만 기록하므로, 설치할 때 위 권한을 지정합니다.

```bash
install -m 700 -o root -g root seokpan-hybrid-backup /usr/local/sbin/   # 예: 백업 작업 VM
sha256sum /usr/local/sbin/seokpan-hybrid-backup | cut -c1-12            # 표와 대조
```

고칠 때는 이 폴더를 먼저 고쳐 PR로 리뷰받고, 설치한 뒤 해시를 대조하고, 표의 해시를 같은 PR에서 갱신합니다.

## 백업 (`seokpan-hybrid-backup`, Timer `*:00/15`)

순서: flock → `paused` 확인 → NTP 동기화 · 시계 오차 1초 미만 → 디스크 여유 100MiB → routines · events · triggers 0개 확인 → mariadb-dump · gzip · age → S3 `periodic/` → 복구 DB VM으로 사본 전송 → 기록

- 설정은 `backup.env`를 `source`로 읽습니다. 비밀값은 넣지 않고, DB 접속 정보는 `SOURCE_CNF` 파일에, AWS 키는 `AWS_PROFILE` 프로필에 둡니다.
- `S3_BUCKET`이 비어 있으면 S3를 건너뛰고 PARTIAL로 기록합니다(foundation Apply 전 예행).
- `/etc/seokpan-hybrid-backup/paused`가 있으면 DB에 접속하지 않고 SKIP으로 정상 종료합니다. 이 파일은 RDS Stop/Start Runbook(#45)만 만들고 지웁니다.
- `/srv/seokpan-hybrid-backup/state/last-success`(UTC 시각 · Backup ID · SHA-256)는 S3 업로드와 복구 DB VM 사본 확보가 모두 성공했을 때만 갱신합니다. 시각은 덤프 시작 시각입니다.
- 결과는 `history.tsv`에 OK / PARTIAL / SKIP / FAIL로 남깁니다. 전송 · 확보 실패는 SKIP으로 바꾸지 않습니다.
- 로컬 `periodic` 암호문은 `KEEP_DAYS`(7)일 뒤 정리합니다.

## 사본 받기 (`seokpan-recv-copy`)

복구 DB VM `sp-copy` 계정의 강제 명령입니다(`restrict,from="192.168.52.50",command=…`, #46 4-8). Backup ID 형식 검사 → `.part`로 받기 → SHA-256 대조 → `/srv/seokpan-hybrid-recovery/copies/<Backup ID>.sql.gz.age` + `.sha256`으로 저장합니다. 같은 ID는 거부하고, `periodic` 사본은 7일 뒤 정리합니다. 응답은 `OK` · `EXISTS` · `SUM-MISMATCH` · `BAD-ID`입니다.

## 복원 (`restore.sh` · `table_hashes.sh`)

`restore.sh`는 복원 Runbook(#48)의 0~4단계를 한 번에 실행합니다. 사본 자동 선택 → SHA-256 → controller에서 age 해독(스트림, 평문 SQL을 디스크에 남기지 않음) → 복구 DB VM에 가져오기 → Revision · 테이블 8개 · 복구 계정 6개 확인 순서입니다. `--replace`가 없으면 기존 `stone_game`을 덮어쓰지 않습니다.

`table_hashes.sh`는 8개 테이블의 행 수 · 행 해시 · Schema 해시를 출력해 원본과 복원본을 비교합니다. 장애 시에는 원본이 없으므로 SHA-256 · 해독 · 가져오기 종료 코드 · Revision · 테이블 8개로 판정합니다(#48).

## 저장소에 넣지 않는 것

실제 `backup.env` · `source.cnf`(DB 접속 정보) · age 개인키 · SSH 키 · SOPS 파일 · 측정 기록(`*.times` · `*.tsv`) · 백업 사본.

## 검증 기록

- 2026-10-08 온프렘 예행(S3 제외): 첫 실행 PARTIAL · 복원 0~4단계 23초 · 원본과 행 수 · 행 해시 · Schema 해시 일치, Timer 4회 15분 간격, SKIP · FAIL 경로 확인 — #48
- S3 경로 · RDS 원본 · 실패 사본 재전송은 foundation Apply 후 검증

관련: #45(RDS Stop/Start · 백업 약속) · #46(복구 DB VM) · #48(복원 Runbook) · #44(이관 Runbook)
