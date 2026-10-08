# Data 백업 · 복원 스크립트

온프렘 15분 백업(RDS → age 암호화 사본 → Backup S3 · 복구 DB VM)과 장애 시 복원(controller → 복구 DB VM)에 쓰는 스크립트입니다. 이 폴더가 기준본이며, 설치된 파일은 아래 해시와 같아야 합니다.

## 파일과 설치 위치

| 저장소 파일 | 설치 위치 | 설치 권한 | SHA-256 앞 12자리 |
|---|---|---|---|
| `backup-vm/seokpan-hybrid-backup` | hybrid-backup-01 `/usr/local/sbin/` | 700 root:root | `a1c73c64c219` |
| `backup-vm/seokpan-hybrid-backup.service` | hybrid-backup-01 `/etc/systemd/system/` | 644 root:root | `c5d4df879a19` |
| `backup-vm/seokpan-hybrid-backup.timer` | hybrid-backup-01 `/etc/systemd/system/` | 644 root:root | `ed1afefbbc1a` |
| `backup-vm/backup.env.example` | 견본 → hybrid-backup-01 `/etc/seokpan-hybrid-backup/backup.env` | 600 root:root | (견본, 값은 환경별) |
| `recovery-db-vm/seokpan-recv-copy` | hybrid-recovery-db-01 `/usr/local/sbin/` | 755 root:root | `f400647f9ff8` |
| `controller/restore.sh` | controller(ksh) `~/recovery/` | 700 ksh:ksh | `e6444667ff47` |
| `controller/table_hashes.sh` | controller(ksh) `~/recovery/` | 700 ksh:ksh | `5beac6da6e9c` |
| `controller/check_data_apply.sh` | controller(ksh) `~/recovery/` | 700 ksh:ksh | `51f536301969` |
| `controller/check_backup_gate.sh` | controller(ksh) `~/recovery/` | 700 ksh:ksh | `63e59d8bf0c2` |

Git은 실행 권한을 755 · 644로만 기록하므로, 설치할 때 위 권한을 지정합니다.

```bash
install -m 700 -o root -g root seokpan-hybrid-backup /usr/local/sbin/   # 예: 백업 작업 VM
sha256sum /usr/local/sbin/seokpan-hybrid-backup | cut -c1-12            # 표와 대조
```

고칠 때는 이 폴더를 먼저 고쳐 PR로 리뷰받고, 설치한 뒤 해시를 대조하고, 표의 해시를 같은 PR에서 갱신합니다.

## 백업 (`seokpan-hybrid-backup`, Timer `*:00/15`)

순서: flock → `paused` 확인 → NTP 동기화 · 시계 오차 1초 미만 → 디스크 여유 100MiB → routines · events · triggers 0개 확인 → Backup ID 결정 → mariadb-dump · gzip · age → S3 `periodic/` → 복구 DB VM으로 사본 전송 → 기록

- 설정은 `backup.env`를 `source`로 읽습니다. 비밀값은 넣지 않고, DB 접속 정보는 `SOURCE_CNF` 파일에, AWS 키는 `AWS_PROFILE` 프로필에 둡니다.
- `S3_BUCKET`이 비어 있으면 S3를 건너뛰고 PARTIAL로 기록합니다(foundation Apply 전 예행).
- `/etc/seokpan-hybrid-backup/paused`가 있으면 DB에 접속하지 않고 SKIP으로 정상 종료합니다. 이 파일은 RDS Stop/Start Runbook(#45)만 만들고 지웁니다.
- Backup ID는 사전 검사를 모두 통과한 뒤 덤프 시작 시각 하나에서 `T_DUMP`와 함께 만듭니다. 따라서 Backup ID 시각 = `T_DUMP` = `last-success` 시각이고, S3 Key · 복구 DB VM 사본명 · 복원 후보 순서 · RPO가 모두 이 시각을 씁니다. 사전 검사 단계의 SKIP · FAIL은 아직 Backup ID가 없어 `history.tsv` 2열이 `-`입니다(1열은 시도 시작 시각).
- `/srv/seokpan-hybrid-backup/state/last-success`(UTC 시각 · Backup ID · SHA-256)는 S3 업로드와 복구 DB VM 사본 확보가 모두 성공했을 때만 갱신합니다. 시각은 덤프 시작 시각입니다.
- 결과는 `history.tsv`에 OK / PARTIAL / SKIP / FAIL로 남깁니다. 전송 · 확보 실패는 SKIP으로 바꾸지 않습니다.
- **S3 실패 사본 재전송:** S3 업로드에 실패한 회차의 Backup ID는 `state/s3-pending`에 남습니다. 이후 회차의 S3 업로드가 성공하면, 그 회차 백업을 끝낸 뒤 같은 Backup ID의 로컬 암호문을 다시 올립니다(03 문서 3-D.9.5절 실패 재시도). 회당 최대 `RESEND_MAX`(4)개이고, 시작 후 `RESEND_BUDGET`(300)초가 지나면 새 재전송을 시작하지 않습니다. SHA-256 체크섬을 붙여 올리고, S3가 기록한 SHA-256이 로컬과 같을 때만 완료로 봅니다(이미 올라가 있으면 다시 올리지 않음). 결과는 `state/s3-resend.tsv`(시각 · 재전송한 회차 · 대상 Backup ID · `ok` / `ok:already` / `fail` / `drop:no-local` / `drop:bad-id`)에 따로 남기며, 원래 회차의 `history.tsv` 기록(FAIL local-only)은 바꾸지 않습니다. 로컬 사본이 `KEEP_DAYS`로 정리된 ID는 `drop:no-local`로 목록에서 뺍니다.
- 로컬 `periodic` 암호문은 `KEEP_DAYS`(7)일 뒤 정리합니다. `find -mtime +7`은 만 8일이 된 파일부터 지우므로 실제 잔존은 7~8일입니다(받는 쪽 `seokpan-recv-copy`도 같음).

## 사본 받기 (`seokpan-recv-copy`)

복구 DB VM `sp-copy` 계정의 강제 명령입니다(`restrict,from="192.168.52.50",command=…`, #46 4-8). Backup ID 형식 검사 → `.part`로 받기 → SHA-256 대조 → `/srv/seokpan-hybrid-recovery/copies/<Backup ID>.sql.gz.age` + `.sha256`으로 저장합니다. 같은 ID는 거부하고, `periodic` 사본은 7일 뒤 정리합니다. 이 정리는 일반 사본만 다루며, 마지막 검증 사본 · 별도 보호 사본의 보존은 03 문서 3-D.9.6절의 소유자 · 보존 경로에서 따로 확인합니다(이 스크립트로 완료되지 않음). 응답은 `OK` · `EXISTS` · `SUM-MISMATCH` · `BAD-ID`입니다.

## 복원 (`restore.sh` · `table_hashes.sh`)

`restore.sh`는 복원 Runbook(#48)의 0~4단계를 한 번에 실행합니다. 사본 선택 → SHA-256 → controller에서 age 해독(스트림, 평문 SQL을 디스크에 남기지 않음) → 복구 DB VM에 가져오기 → Revision · 테이블 8개 · 복구 계정 6개 확인 순서입니다. `--replace`가 없으면 기존 `stone_game`을 덮어쓰지 않습니다.

- **자동 선택:** 운영 `periodic-YYYYMMDDTHHMMSSZ` 사본만 대상으로, Backup ID의 데이터 시각(덤프 시작, UTC)이 가장 최근인 것부터 고릅니다. 파일 도착 시각(mtime)은 보지 않습니다. SHA-256이 맞지 않으면 바로 이전 `periodic` 사본으로 넘어갑니다. 형식이 틀리거나 미래 시각(5분 초과)인 이름은 제외합니다.
- **예행 사본:** `rehearsal-*`는 `--rehearsal`(자동 선택 대상을 예행 사본으로) 또는 `--id`로 지정했을 때만 선택되고, 결과와 기록에 `TEST`로 남습니다. 운영 판정에 쓰지 않습니다.
- **`--id`:** 원격 명령에 넣기 전에 형식 · 날짜 · 미래 시각을 검사하고, 틀리면 종료 코드 2로 멈춥니다. 지정한 사본의 SHA-256이 틀리면 다른 사본으로 대체하지 않고 멈춥니다.
- **`--select-only`:** 1단계(선택 · SHA-256)까지만 실행하고 선택 결과만 출력합니다. 복구 DB를 바꾸지 않습니다.

`table_hashes.sh`는 8개 테이블의 행 수 · 행 해시 · Schema 해시를 출력해 원본과 복원본을 비교합니다. 조회가 하나라도 실패하거나(접속 실패 · 쿼리 오류 · PK 없음) 테이블 수가 `--expect-tables` 값과 다르면 stderr에 이유를 쓰고 종료 코드 1로 끝나며, `restore.sh`는 그때 T4로 진행하지 않습니다. 장애 시에는 원본이 없으므로 SHA-256 · 해독 · 가져오기 종료 코드 · Revision · 테이블 8개로 판정합니다(#48).

## Apply 당일 확인 (`check_data_apply.sh` · `check_backup_gate.sh`)

둘 다 조회만 하고, SG ID · VPC ID · 계정 번호 · Endpoint 주소를 출력하지 않습니다. 결과(OK · NG 줄)를 Issue에 그대로 남길 수 있습니다. 사용 순서는 이관 Runbook(#44) 0-1행과 10장입니다.

- `check_data_apply.sh` — foundation Apply 직후. Data SG 2개를 ID가 아니라 이름(`seokpan-fnd-rds` · `seokpan-fnd-redis`)으로 찾아 VPC · `Component=data` · 규칙(온프렘 `/32` · Worker · 그 밖의 규칙 없음 · egress 없음)을 보고, RDS · Valkey · Backup S3 · Backup User 설정을 코드 값과 대조합니다. `RDS_SG_ID` · `REDIS_SG_ID`로 rosa에 넘긴 ID를 주면 서로 바뀌지 않았는지도 봅니다. rosa Apply 뒤에는 `ROSA_APPLIED=1`.
- `check_backup_gate.sh` — RDS 원본 백업의 Timer 회차가 2번 OK로 쌓인 뒤. PR #53 운영 Gate(Backup ID 시각 = `T_DUMP` = `last-success`, history · S3 객체 · 복구 DB VM 사본의 SHA-256, 15분 간격, 로컬 확보 지연, 데이터 나이 30분)를 확인합니다. 백업 작업 VM은 SSH 1회로 읽습니다.

## 저장소에 넣지 않는 것

실제 `backup.env` · `source.cnf`(DB 접속 정보) · age 개인키 · SSH 키 · SOPS 파일 · 측정 기록(`*.times` · `*.tsv`) · 백업 사본.

## 검증 기록

- 2026-10-08 온프렘 예행(S3 제외): 첫 실행 PARTIAL · 복원 0~4단계 23초 · 원본과 행 수 · 행 해시 · Schema 해시 일치, Timer 4회 15분 간격, SKIP · FAIL 경로 확인 — #48
- 2026-10-08 실패 사본 재전송 · 확인 스크립트 2개: 실제 경로에 설치한 스크립트를 가짜 DB · age · S3 · SSH로 실행해 재전송 8가지(실패 후 복구 · 회당 상한 · 이미 올라간 사본 · 로컬 정리된 ID · 잘못된 ID · S3 해시 불일치 · 시간 예산 · `S3_BUCKET` 빈 값의 기존 동작)와 확인 스크립트의 정상 · 결함 경우를 확인. 실제 AWS에서는 자원이 없는 상태로 `check_data_apply.sh` 호출 7가지가 "찾지 못함"으로 끝나는 것까지 확인
- S3 경로 · RDS 원본 · 실패 사본 재전송의 실제 S3 동작은 foundation Apply 후 검증

관련: #45(RDS Stop/Start · 백업 약속) · #46(복구 DB VM) · #48(복원 Runbook) · #44(이관 Runbook)
