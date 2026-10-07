# 독립 로컬 Backup/Restore와 Backend 업무 예행

`recovery_fixture`의 부분 Data 측정에 새 Redis TLS/AUTH와 실제 Production Backend Provider 조립·HTTPS 업무 경로를 연결하는 **부분 T18 예행**이다. 실행 자산은 Infra에 두고 App 코드를 수정하지 않는다. ROSA, AWS 계정, 실제 공유 DB, Registry, Kubernetes, 기존 1차 서비스에 접근하거나 유료 환경을 생성하지 않는다.

## 조건과 실행

- Linux에서 MariaDB CLI/server/install-db, age/keygen, OpenSSL, TLS 지원 Redis server를 사용할 수 있어야 한다. Tool prefix는 `recovery_fixture`와 같은 PATH/LD_LIBRARY_PATH/SEOKPAN_FIXTURE_TOOL_PREFIX로 지정한다.
- App Checkout은 `seokpan/seokpan-hybrid-app`의 `c12b3d15a4dd2c806fac4326a9eb30ed6e8a81b3`로 고정한다. Git HEAD/추적 변경 검사를 하거나, 이 검토에서 가져온 390 Blob Manifest의 고정 SHA-256과 모든 Blob을 확인한다. 임의 App 변경·최신 Branch를 자동으로 사용하지 않는다.
- 해당 App의 Python 3.13.15/uv 0.12.5와 `uv.lock`에 맞는 `backend/.venv`를 장애 전에 준비한다. Harness는 Python/asyncmy/redis 버전을 확인하며 환경 설치·Image Build/Push를 실행하지 않는다.
- Output은 매번 새 Run 디렉터리여야 하며, `--infra-sha`에는 검토한 Harness Commit 전체 SHA를 기록한다.

```bash
python tools/recovery_business_fixture/run.py \
  --app-checkout /path/to/verified-hybrid-app \
  --operator ACTUAL_EXECUTOR_ID --run-id T18-local-backend-business-NEW-ID \
  --infra-sha FULL_REVIEWED_INFRA_COMMIT \
  --output /path/to/new-evidence-run
```

`summary.md`, `release.json`, `metrics.csv`, `timeline.csv`, `checksums.txt` 다섯 파일만 공개 증거로 사용한다. 백업·SQL·가상 Password·Key·Cookie·Process 진단은 생성한 전용 임시 경로에만 두고 실행 후 삭제한다. 실패 Run도 새 디렉터리에 실패 범위와 정제한 오류 유형을 남긴다.

## 실제 수행 범위

새 Loopback TCP MariaDB 두 개를 Harness가 직접 생성하고 Datadir/Port/생존 PID와 빈 DB를 확인한다. App Revision 기반 Schema와 가상 데이터에 인증 가능한 가상 Member Hash를 넣는다. Quiescent Dump→gzip→age 사전 백업을 준비한 뒤, 해독→새 DB Import→Schema/행/Revision 비교→별도 SSL 요구 계정 생성→새 TLS-only Redis의 AUTH/DB0 공백 확인→Production Backend의 CA/Hostname 검증과 Runner Ready→HTTPS 재로그인/전적·Rating 조회→새 Room/Join/Team/Ready/Game→명시적 이탈/기권 결과 조회→새 결과와 Rating SQL 확인을 수행한다.

DB/Redis/HTTPS 인증서는 별도의 일회성 가상 CA/localhost SAN을 사용한다. Redis는 Plaintext Port 0이고 새로운 가상 AUTH를 쓴다. Fixture Account 권한은 이 제한된 시험용이며 C의 운영 계정·TLS/Host 검토를 대신하지 않는다. 가상 기존 Draw/Move 기록은 행 비교 자료이며 실제 완료 게임의 도메인 유효성까지 증명하지 않는다.

## 판정 경계와 담당 기록

기존 완료 DB 기록 보존·SQL 비교와 새 Runtime의 로그인/전적/신규 게임·새 완료 결과 확인을 구분한다. **기존 개별 게임 결과의 사용자 조회**는 새 Redis에 예전 Room/Participation이 없어 현재 App API에서 제공되지 않는다. 이를 통과시키려고 Redis 상태를 재구성하지 않는다. 승인된 Docs PR #30·03 §3-I.14.4(기능 경계)·§3-I.14.5(목표)에 따라 과거 영속 기록은 SQL/관계로, 신규 로그인·랭킹/누적 기록·새 게임/현재 결과는 클라이언트로 구분한다. 과거 개별 결과 HTTP/UI 신설은 채택된 복구 Must가 아니며 설계 승인을 다시 기다리지 않는다. 이 기능 경계 확정이 전체 T18 수락은 아니다.

스크립트가 측정한 `scripted_continuation_seconds`는 준비된 사본 해독 이후의 처리 시간이다. 장애 탐지/사람 판단/대기·실제 Host/Harbor/Image/PVC/OCP/사용자 안내·FE/브라우저/WSS·RDS/S3/VPN은 포함하지 않는다. 따라서 서비스 RTO/RPO, 승인 Release, 최종 T18 Acceptance로 기록하지 않으며 관련 필드는 null/NOT RUN이다. MariaDB/Redis Package 버전 차이·동일 Host 파일 경로·실제 Cloud 부하 부재를 기록한다.

C 김상희의 Data 책임, A 이유빈의 실제 Host/Storage, B 정태훈의 App/접속, D 최유준의 Image/증거 책임을 유지한다. 실제 실행자가 Codex일 때는 tjung03의 허용 범위에서 수행한 기여로 남기며 팀원의 실행·검토를 대신했다고 적지 않는다. C/B/A/D 검토와 실제 환경 후속 증거는 원래 Issue/PR 및 새 Run에서 별도로 연결한다.

## 실행 주체와 검증 범위

새 Run은 `--operator ACTUAL_EXECUTOR_ID`로 실제 수행 주체를 명시한다. 이 값은 기록용 표기이며 인증이나 리뷰·인계 수락 증거가 아니다. 기존 Run을 덮어쓰지 않고 새 Run ID/출력 경로를 사용한다. 고정된 과거 App/Redis 조합을 재현하는 부분 fixture이며 Valkey7.2·전체 T18 또는 DR10분/영속 DB RPO30분·15분 Backup 달성을 검증한 것으로 승계하지 않는다. 기존 Evidence는 변경하지 않는다. 새 `release.json`의 `known_limitations`에는 실제 fixture 도구 버전과 운영 버전/Valkey 조합 차이, 로컬 시험 계정과 RDS backup_dump 권한 차이, 일회 실행과 15분 운영 백업의 차이를 기록한다. 기존의 C 합성 자료 검토 수신과 실제 운영 Data 검토는 별개이므로 `C_OPERATIONAL_DATA_REVIEW`는 유지한다.
