# 발표 계측 실행 연결

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/pull/140).

## 범위와 준비 상태

Infra PR85의 병합 개정 `df71963f975cae2925f8783472a8c143b5f264af` 위에 별도로 추가한 계측이다. PR85 승인을 이 코드의 승인으로 간주하지 않는다. 이 문서는 실제 재생성/SQL/Sync/시험 시작 지시가 아니다. 기동 전 준비, 실행 시 얻는 실제 값, 실측 수락을 구분한다.

선정 결과는 단계별 배포 전환시간, 정상 대비 Backend 롤링 영향, Data 연결 예산/실측 세 가지다. 기존 정상 브라우저 도구·설치/Pull/TLS/로그인/UI/smoke/CNI/Recovery 시험은 변경하거나 반복하지 않는다. 원본의 Source 패치/명시 Sync/실제 교체가 필요할 때만 해당 실행을 관측한다.

## 담당과 전달물

| 구간 | 실행 위치/담당 | 준비된 명령과 성공 판정 | 전달/보존 |
|---|---|---|---|
| 최초 stage 전환 | Controller jth / B | `stage_clock.py entry` → 관측기를 먼저 띄운 뒤 기존 Argo 비교/명시 Sync → `observe` → 외부 확인 수신 | stage별 JSONL, external request/result, Ready·Source/Release |
| 입력 결합 | Controller / B | `bind_inputs.py`, 로컬 준비 status·missing 목록. 현재 환경 성공 판정 아님 | baseline/rolling/rolling-config/trigger/DB 두 입력과 SHA256SUMS |
| 단일 Backend 롤링 | Controller / B | `rolling_control.py prepare/trigger/observe/verify`, 최종 `PASS_BACKEND_ROLL_RECONCILED_NOT_BUSINESS_ACCEPTANCE` | trigger 표식, before, Pod/Node 시간선, rolling-verified.json |
| 정상/롤링 발생기 | 기존 측정 PC 또는 D 발생기 / B·D 실행 경로 연결 | 같은 별도 observer로 baseline/rolling 각 6명·2방, 준비60/고정180초; 요청+30초1회 | receipt/counts/raw/Room/resource와 SHA256SUMS |
| DB 관측 | 기존 RDS TLS 접근 호스트 / 기존 경로를 B가 연결 | `db_observation.py sample/summary`, 현재 G/계정 한도·연결수/주기/누락 | run별 DB JSONL, 실제 한도/예약 근거와 예산 결과 |
| 짝 비교·공개 요약 | 측정 결과 호스트 / B | `compare_runs.py`에 실제 rolling verification 필수 | 같은 UID/Source/Image/Replica의 짝 결과, 정제한 기술 요약 |

새 보고서/새 발생기 구현이나 완료한 시험 재실행은 요청하지 않는다. 기존 실행 위치·개정·명령·보호 경로를 받는다. 자격값은 Git/채팅/실행 argv에 넣지 않는다. 내부 경로가 포함된 입력과 원본은 보호 보관하고 GitHub에는 코드 및 정제한 기술 결과만 올린다.

## 실행 전 연결 순서

1. 채택할 이 PR의 Source를 리뷰 후 고정한다. Controller와 발생기에는 같은 고정 개정을 내려받는다. Python3, Node20 이상, 기존 Chrome/Playwright 경로를 사용하며 이 도구가 설치/인증을 대신하지 않는다.
2. 현재 Foundation/Data·비용/종료 시각을 기존 기동 입력에 반영한다. 실제 생성은 별도 시작 지시 뒤 기존 lifecycle 명령으로 진행한다. 이 계측은 lifecycle을 호출하지 않는다.
3. 새 플랫폼과 목적 SQL/Schema·모드/Pool 입력을 기존 prestart에 연결한다. 각 stage **첫 실행 전** 아래 wrapper를 사용한다. 기존 stage 명령에 추가로 다시 실행하는 것이 아니다.
4. 아래의 단계 관측·외부 확인을 마치고 정상 업무를 수락한 뒤 `bind_inputs.py`로 이번 측정 입력을 생성한다. 생성 전에는 실제 값을 추측해서 채우지 않는다.
5. 정상과 롤링은 같은 이미지·기본 Replica·설정으로 한 번씩 관측한다. DB 및 Pod/Node 수집을 같은 Run에 연결한다. 실제 Backend 교체 확인 후에만 짝 비교한다.
6. 증거/한계 보존 뒤 별도 삭제 확인을 받는다. NAT/EIP/Foundation/Data/보존 Snapshot은 이 도구가 변경하지 않는다.

## 최초 stage 실행부터 계측

Controller의 기존 prestart 변수 `B_INFRA_REPO`, `B_CLOUD_READY_WORK`, `B_READY_CONFIG`를 사용한다. `B_STAGE`는 선정한 single/shared/target 중 하나, `B_CLOCK`은 해당 stage의 신규 JSONL 절대 경로다.

```bash
# 기존 stage --execute를 대신하는 진입점. 생성/백업 명령이 아니다.
B_MEASURE="$B_INFRA_REPO/tools/presentation-measurement"
python3 -I "$B_MEASURE/stage_clock.py" entry \
  --infra "$B_INFRA_REPO" --work "$B_CLOUD_READY_WORK" \
  --ledger "$B_CLOCK" --stage "$B_STAGE" -- --config "$B_READY_CONFIG" --execute
# 공유/target 진입에 필요한 기존 업무 근거는 prestart의 --business-evidence 인수로 함께 전달한다.
```

Source가 준비되면 다른 Controller 터미널에서 관측기를 실행하고, 그 뒤 생성된 기존 Argo Console 코드를 사용해 **이번 Source만** 비교/명시 Sync한다. 완료한 과거 비교를 반복하지 않는다.

```bash
python3 -I "$B_MEASURE/stage_clock.py" observe \
  --infra "$B_INFRA_REPO" --work "$B_CLOUD_READY_WORK" --ledger "$B_CLOCK" \
  --stage "$B_STAGE" --kubeconfig "$B_PERSONAL_KUBECONFIG" --oc "$B_OC" --interval 2 --seconds 600
```

외부 request 파일 전달·Windows/발생기 실행·Controller 수신은 [도구 README](../tools/presentation-measurement/README.md)의 명령을 따른다. s0는 실제 stage 진입, s4는 Controller가 외부 결과를 대조·수신한 시각이다. 전달/수동 대기가 포함된다. 외부 호스트와 Controller의 서로 다른 시계를 빼지 않는다. 이미 실행한 stage에서 시작 기록이 없다면 미측정으로 남기며 재실행하지 않는다.

## 실제 Ready 결과를 측정 입력으로 결합

`execution-paths.TEMPLATE.NOT_ACCEPTED.json`의 경로만 B가 기존 인계값으로 채운다. `controller_trigger_prefix`는 발생기에서 Controller에 접근할 기존 argv 배열이다. 직접 Windows→Controller 연결이 된다고 가정하지 않는다. Controller 자체에서 실행할 때만 명시적 빈 배열 `[]`을 선택하며 미정은 null이다. SSH 경로를 선택하면 비대화형 인증·host trust는 기존 보호 경로로 준비해야 한다. 이 binder는 새 키/신뢰를 발급하지 않고 원격 연결 성공도 주장하지 않는다.

```bash
# B_BOUND_OUT은 신규 디렉터리. controller_work에 적은 위치와 같게 선택하거나 그대로 전송한다.
python3 -I "$B_MEASURE/bind_inputs.py" \
  --infra "$B_INFRA_REPO" --work "$B_CLOUD_READY_WORK" --stage "$B_STAGE" \
  --business-ref "$B_NORMAL_BUSINESS_REF" --prestart-config "$B_READY_CONFIG" \
  --paths "$B_EXECUTION_PATHS" --out "$B_BOUND_OUT"
```

이 명령은 검토된 launch 모듈의 패키지/Ready 결합을 재사용한다. 새 Namespace/Application UID·Source·Image·Replica·Pool/config/Schema·기존 실행시간을 가져와 baseline.json, rolling.json, rolling-config.json, trigger.json, db-baseline.json, db-rolling.json을 만든다. 기존 업무 근거 참조의 내용 자체를 자동 수락하지 않는다. 현재 Ready를 새로 조회하는 명령도 아니다. 입력/출력 checksum을 보존하며 부족한 경로는 `PENDING_LOCAL_EXECUTION_PATH_INPUTS`/exit2로 표시한다. 기존 출력 위에 덮어쓰지 않는다.

DB 자격 파일은 기존 MariaDB `[client]` 형식과 CA의 경로만 필요하며 binder가 읽거나 복사하지 않는다. G/계정 한도/예약 및 steady/surge/terminating 예산 값은 별도 현재 근거로 채운다. 기록이 없는 종료 중 Pod를 0으로 가정하거나 과거38/68을 복사하지 않는다.

## 실패·재개와 해석 제한

- Source/Sync/trigger 결과 불명이면 변경 명령을 재제출하지 않는다. 기존 표식/Source/operation/현재 Pod를 대조하고 observe 경로로 이어간다.
- 최초 s0·Run/UID/Source 결합·Room generation을 보존한다. 호스트 재부팅 뒤 monotonic 시간선은 이어 계산하지 않는다.
- observer 중단 파일과 Controller pause/resume/recover 절차는 README를 따른다. 진행 중 Argo operation은 해당 작업만 종료·대조한 뒤 pause한다. annotation 제거의 추가 복구 롤을 원 비교로 합산하지 않는다.
- 실제 새 Backend Ready·구 Pod 종료·FE Pod UID 유지가 확인되지 않으면 롤링 비교표는 불완료다. HTTP/업무 분모와 S/F/U·재시도, 고정 구간/추가 수렴 관측을 분리한다.
- 유효 표본0, 업무 시도0, 잘못된 Room/Game 프레임, 다른 Source 결과 혼합은 정상값을 만들지 않는다. 계정 가시성·짧은 peak·표본 누락은 null/미관측으로 남긴다.
- 이 수치는 통합 경로·운영 설정의 타당성/교체 중 사용자 영향을 설명한다. ROSA 구축시간·개선율·최대 부하·Kubernetes 자체 기능의 개인 성과·전체 DR 수락으로 표현하지 않는다. 발표 구성/순서를 이 코드가 확정하지 않는다.

## 준비 검토 근거

최초 계측 준비의 연쇄 검토에서 시작 시각, 미완료 HTTP 분모, 실행 전 trigger 검사, Room/Game 결합, 실제 롤링 확인, 결과 checksum/Run 결합을 보완했다. 기존 로컬 Python32/Node20 합성52건 PASS를 재사용하고 이 게시 작업에서는 새 입력 결합8건만 로컬 실행했다. GitHub CI는 게시된 정확한 Source에서 총 Python40/Node20과 manifest·문법을 확인한다. 실환경 성과나 실제 생성/SQL/Sync/측정 성공을 뜻하지 않는다.
