# 발표 B 결과 확보용 새 계측 준비

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/pull/140).

이 디렉터리는 2026-10-19에 전달받은 세 결과 요구를 위한 **별도 구현·연쇄 추적 검토·회귀 검증본**이다. 기존 PR85는 df71963으로 병합됐으며 이 새 계측의 승인을 포함하지 않는다. 기존 정상 측정기는 변경하지 않았다. 별도 PR89의 승인·병합 이후 실제 정상 Baseline은 완료했고 롤링은 준비 중 중단됐다. 완성된 단계별 배포 전환시간과 실제 롤링 결과는 미확보로 남긴다. 전체 실행 연결·담당·판정은 [실행 문서](../../docs/PRESENTATION_MEASUREMENT_RUN.md)를 따른다. 이 문서의 명령은 지금 ROSA를 생성하거나 앱/시험을 시작하라는 뜻이 아니다.

## 소스와 실행 환경

- 채택 배포/선행 도구: Infra 병합 개정 df71963f975cae2925f8783472a8c143b5f264af(PR85). bind_inputs.py는 이 launch 모듈의 canonical SHA256을 확인하고 읽기 전용 함수를 재사용한다.
- GitOps: c75a4b5e3b3b9bae4ea7d8a431962cb9ca7308d7. monitoring은 별도 고정16fd14cd1c085a8314a5e2e18b89e2cd3b051626.
- 정상 UI 어댑터: App c2dad254cc407431092691f9ea5892f8324f1520 대응. vendor/cloud-business.mjs·measurement.mjs는 기존 파일 그대로다. 정상 runCloud의 소켓 유실 중단 판정은 변경하지 않는다.
- DB 예산: Docs tools/b_preflight/db_connection_budget.py를 vendor에 그대로 보관하고 기존 review()를 호출한다. 현재 main 파일 본문도 대조했다. 새 함수는 계정별 상한/실제 관측을 따로 붙인다.
- Controller 기준 시계: Linux Python3, 동일 호스트/boot ID의 monotonic clock. 브라우저: 기존 Windows 측정 PC Node24·Chrome·Playwright1.63.0. DB 수집: A가 이미 사용하는 RDS TLS 접근 호스트의 MariaDB CLI.
- 이 도구는 계측을 위해 브라우저/DB 클라이언트를 Controller에 설치하지 않는다. 기존 PC 또는 D 발생기의 실행 경로를 선택한다.

## 1 단계별 전환 시간

Controller에서 처음 실행할 stage 명령을 아래 wrapper로 감싼다. 기존 선행 준비·SQL은 원래 순서대로 진행하되 실제 stage_app 호출 직전에 최초 s0를 기록한다. 이미 stage가 실행됐다면 s0를 복구하려고 재실행하지 않는다. 기존 시각이 없으면 미측정이다.

```bash
# B_INFRA: 채택 Infra checkout, B_MEASURE: 이 도구를 전달한 절대 경로
# B_READY_CONFIG/B_WORK: 기존 cloud-ready 보호 입력/같은 고정 실행 디렉터리
# B_CLOCK: 이번 stage 전용 신규 JSONL. single/shared/target마다 다르게 둔다.
python3 -I "$B_MEASURE/stage_clock.py" entry \
  --infra "$B_INFRA" --work "$B_WORK" --ledger "$B_CLOCK" --stage single \
  -- --config "$B_READY_CONFIG" --execute
```

**다른 Controller jth 터미널에서 아래 관측을 먼저 시작한 뒤** 기존 전체 선언 비교/명시 Sync 코드를 새 Argo Console에서 실행한다. 자동 Sync는 추가하지 않는다. 정확 stage와 해당 kubeconfig를 사용한다. 대상이 다른 기존 비교를 반복하지 않는다.

```bash
python3 -I "$B_MEASURE/stage_clock.py" observe \
  --infra "$B_INFRA" --work "$B_WORK" --ledger "$B_CLOCK" --stage single \
  --kubeconfig "$B_PERSONAL_KUBECONFIG" --oc "$B_OC" --interval 2 --seconds 600
```

기존 Source/readback이 확인한 Application UID·Source·이미지·Replica/generation·Route에 결합한다. 출력 `READY_FOR_EXTERNAL_HTTPS_WSS_ONLY`의 request_file에는 자격값이 없다. 이를 Windows/D 발생기에 전달한다. 사전 계측을 놓친 s2는 실제 시작으로 소급하지 않는다. operation 원 UTC와 기준 호스트 첫 관측을 별도로 남기며, 이미 Succeeded에서 처음 봤다면 Sync 시작 관측 누락으로 표시한다. 화면 시각끼리 직접 빼지 않는다.

```powershell
# Windows 측정 PC. 이 디렉터리에 자격/브라우저 세션을 복사하지 않는다.
# 실행 파일과 모듈은 이미 확인된 기존 설치 경로를 사용한다.
& $B_Node "$B_Measure\run-observer.mjs" --request $B_ExternalRequest `
  --playwright-module $B_PlaywrightModule --out $B_NewExternalOut --execute
```

이 확인은 같은 HTTPS Origin의 문서200 및 Guest 정상 진입의 lobby WSS 초기 snapshot을 확인한다. 방/회원은 만들지 않는다. 업무 전체나 Room 복구를 뜻하지 않는다. 결과 external-result.json을 Controller에 전달해 같은 stage에 수신한다.

```bash
python3 -I "$B_MEASURE/stage_clock.py" external \
  --ledger "$B_CLOCK" --result "$B_EXTERNAL_RESULT"
```

s4는 기준 호스트가 해당 결과를 수신·대조한 시점이다. 외부 확인·파일 전달의 수동 대기가 전체 시간에 포함된다. 외부 호스트 UTC를 Controller monotonic에서 빼지 않는다. 외부 완료부터 전달까지 지연은 별도 정확값이 없으면 null이며, 이 기록을 서비스 응답시간이나 무대기 배포시간으로 표현하지 않는다. 부팅이 바뀌면 같은 시간선을 이어 계산하지 않는다. 재개는 기존 ledger/동일 work를 사용하고 최초 s0를 보존한다. 여러 기록기가 동시에 붙어도 append 잠금을 사용한다.

기존 cloud-ready --after-sync는 출력된 ready receipt를 재사용한다. 이미 수락한 SQL·보호 공급·Data Pod 생성을 다시 하지 않는다. 단계별 정상 업무를 수락한 뒤 shared/target으로 넘어간다. 각 stage의 시간은 별개다.

## 2 정상 대비 Backend 롤링

같은 채택 target의 이미지·Replica·설정·6클라이언트/2방·준비60초/고정180초를 정상과 롤링 각각 사용한다. 두 실행 사이에 다른 Data 설정/장애/FE 변경을 하지 않는다. 정상 코드를 느슨하게 고치지 않고 fault-observer.mjs의 별도 경로로 **두 실행을 모두** 관측한다.

먼저 Controller에서 같은 Application UID/Source·실제 FE/BE 이미지/기본 Replica를 묶어 읽기 전용 계획을 만든다. `--execute` 없는 명령은 입력 확인뿐이다. config는 아래 template을 B가 실제 새 값으로 작성한다. 실제 source hash는 정규화 값을 사용한다.

```bash
python3 -I "$B_MEASURE/rolling_control.py" prepare \
  --config "$B_ROLL_CONFIG" --work "$B_ROLL_WORK" --execute
# 다른 터미널: Pod UID/Node/Replica/종료 중 Pod를5초 간격으로 수집한다.
python3 -I "$B_MEASURE/rolling_control.py" observe \
  --config "$B_ROLL_CONFIG" --work "$B_ROLL_WORK" --seconds 600 --execute
```

정상 실행 후 동일 profile의 scenario=rolling으로 실행한다. 발생기에서 Controller의 **아래 trigger 한 명령**을 실행할 수 있는 기존 경로를 D/B 한 명에게 연결한다. trigger JSON의 argv는 shell 문자열이 아닌 인수 배열이다. scope=ONE_BACKEND_ROLL 및 이번 namespace/application UID를 묶는다. Windows의 과거 Controller 직접 SSH 실패 경로가 해결됐다고 가정하지 않는다. 기존 접근 가능한 D 발생기/보호 SSH 경로를 선택하고 원격 실행·출력 경로를 채운 뒤 실행한다. 이것은 가동 전 실제 연결 입력이다.

```bash
# 발생기의 trigger adapter가 관측 시작+30초에 딱 한 번 호출한다.
python3 -I "$B_MEASURE/rolling_control.py" trigger \
  --config "$B_ROLL_CONFIG" --work "$B_ROLL_WORK" --execute
```

trigger는 이미지/기본 Replica/FE를 바꾸지 않고 Application Source의 Backend pod-template annotation 하나와 명시 operation을 제출한다. Prune=false/retry0. Source와 GitOps 실행을 함께 기록하며 `oc rollout restart`를 별도로 더 실행하지 않는다. Backend template의 다른 차이나 FE 변경은 차단한다. 실제 시작 요청 시각·응답 수신 시각과 Pod 사건을 따로 보존한다. +30초는 발생기 요청 목표이며 실제 실행 오차는 기록값으로 표시한다.

```powershell
# 먼저 baseline JSON, 다음 동일 조건 rolling JSON. 둘의 out 디렉터리는 다르다.
& $B_Node "$B_Measure\run-observer.mjs" --plan $B_NormalPlan `
  --playwright-module $B_PlaywrightModule --out $B_NormalOut --execute
& $B_Node "$B_Measure\run-observer.mjs" --plan $B_RollingPlan `
  --playwright-module $B_PlaywrightModule --trigger $B_TriggerJson `
  --abort-file $B_AbortFile --out $B_RollingOut --execute
```

기존 measurement plan에 scenario, extra_recovery_seconds(0~120), binding(실제 cluster/ns/app UID/Source)를 붙인다. normal/rolling의 run_id만 구분하고 binding/release/runtime/workload는 동일하게 유지한다. 원 HTTP와 식별 가능한 request-id 재시도, 업무 S/F/U 분모는 따로 산출한다. HTTP 재시도를 식별할 수 없는 요청은 NOT_IDENTIFIABLE로 남긴다. 전송 실패/5xx/응답 불명은 업무 U이며 임의 재시도하지 않는다. 확정 거부는 현재 검증 가능한 STALE_STATE409만 F로 분류하고 다른 응답은 보수적으로 U에 남긴다.

Room 상태 WSS만 generation을 기록한다. 최초 종료 시각을 실패 재연결로 덮어쓰지 않는다. 새 초기 snapshot/최신 관측 game version과 UI turn·보드·투표 집계가 맞아야 수렴으로 기록한다. DOM 자체에 version 속성이 없으므로 DOM version 직접 대조라고 표현하지 않는다. 재연결 중인 방은 새 투표를 만들어내지 않으며 중단 시간은 고정180초에 계속 포함된다. 원시도 수가 없으면 불완료다. 180초 뒤 수렴 관측은 별도 recovery 구간이며 비교 통계에 섞지 않는다. 연결 종료가 없으면 N/A이고 0초 복구가 아니다.

```bash
# 결과 응답이 불명이면 trigger 재호출 금지. observe와 marker/현재 Source부터 대조.
# 즉시 관측 중단: 발생기의 B_AbortFile을 생성한다. 브라우저/자기 방만 정리한다.
# 진행 중 Backend rollout을 멈춰 검토해야 하는 경우 한 번만:
# 먼저 Argo에 Running operation이 있으면 새 Argo 화면에서 해당 operation만 Terminate하고
# 종료 상태를 확인한다. 그 작업이 남아 있으면 아래 pause/resume은 차단한다.
python3 -I "$B_MEASURE/rolling_control.py" pause \
  --config "$B_ROLL_CONFIG" --work "$B_ROLL_WORK" --execute
# pause는 live paused=true의 임시 GitOps 차이다. 현재 Source/ReplicaSet 대조 뒤만 재개:
python3 -I "$B_MEASURE/rolling_control.py" resume \
  --config "$B_ROLL_CONFIG" --work "$B_ROLL_WORK" --execute
# 원래 선언으로 되돌려야 할 때만. annotation 제거로 추가 복구 롤이 생길 수 있다.
# 고정 비교 구간 밖에서 실행하며 두 번째 시험으로 몰래 합산하지 않는다.
python3 -I "$B_MEASURE/rolling_control.py" recover \
  --config "$B_ROLL_CONFIG" --work "$B_ROLL_WORK" --accept-recovery-roll --execute
```

성공적인 롤링 수락은 trigger 접수만으로 내리지 않는다. 현재 Source/operation 종료, 새 Backend ReplicaSet/Ready, FE template 보존, 외부 경로와 수렴·업무 결과를 같은 Run에서 대조한다. PDB/eviction 시험과 Deployment rolling을 혼동하지 않는다. 최종 source hash는 annotation 변경 전후를 별도로 보존한다.

다음 verify는 같은 Source/Sync/Healthy·실제 새 Backend Pod 전부 Ready·기존 Backend Pod 종료·FE template와 Pod UID 유지까지 확인한다. 결과가 없으면 정상/롤링 비교표를 완료로 만들지 않는다. 새 실행에서 처음 확인하는 해당 변경의 판정이며 과거 설치/Pull 시험의 반복이 아니다. 검증 receipt를 받았다면 같은 config의 파일을 재사용한다.

```bash
python3 -I "$B_MEASURE/rolling_control.py" verify \
  --config "$B_ROLL_CONFIG" --work "$B_ROLL_WORK" --execute
```

생성된 rolling-verified.json은 자격값을 담지 않는다. Windows 측정 결과 쪽으로 전달한 뒤 같은 source/image/UID·각 Run의 파일 checksum과 counts/receipt 결합을 확인한다.

```powershell
& $B_Python "$B_Measure\compare_runs.py" --baseline $B_NormalOut --rolling $B_RollingOut `
  --rolling-verification $B_RollingVerified --out $B_NewPairResult
```

원 요청이 끝나지 않은 채 관측이 종료돼도 HTTP 분모에 남긴다. trigger 입력/UID/profile은 브라우저 기동 전에 검사하며 관측 도중 입력 파일을 바꿔 다른 명령을 끼워 넣지 않는다. 다른 Room/Game 상태나 미완성 프레임으로 수렴을 수락하지 않는다. first valid frame은 연결 사용 가능 증거이며 handshake 완료 시각 자체를 측정한 값이라고 표현하지 않는다.

## 3 Data 연결 예산과 실제 수집

현재 steady/surge/terminating Pod를 겹치지 않게 세고 이미지의 Process/각 Engine Pool/overflow를 넣는다. 종료 중 Pod를 모른다고0으로 넣지 않는다. 그룹 내부에 구/신 설정이 섞이면 각 항목의 보수적 최대값과 그 근거를 사용한다. 기본값38/68을 현재 관측으로 복사하지 않는다.

```bash
python3 -I "$B_MEASURE/db_observation.py" budget --config "$B_CURRENT_BUDGET_JSON" \
  --account-limits "$B_CURRENT_ACCOUNT_LIMITS_JSON" --out "$B_NEW_BUDGET_RESULT"
```

A가 사용 중인 RDS TLS 조회 호스트에서 기존 MariaDB defaults 파일(비밀번호 포함 가능하므로 보호 파일)과 CA 경로를 선택한다. 새 관측 권한을 자동 발급하지 않는다. 위 file은 기존 `[client]` 형식이다. URL/다른 형식이면 B가 보호 위치에서 변환하며 채팅에 값을 출력하지 않는다.

```bash
# A 기존 TLS 관측 호스트. 권장 기본5초, 최대 실행900초, 종료시간 강제 제한.
python3 -I "$B_MEASURE/db_observation.py" sample --config "$B_DB_OBSERVER_JSON" \
  --out "$B_NEW_DB_TIMESERIES" --execute
python3 -I "$B_MEASURE/db_observation.py" summary --config "$B_DB_OBSERVER_JSON" \
  --out "$B_NEW_DB_TIMESERIES"
```

전체 한도/계정별 한도 근거와 실제 C(t)는 별개다. PROCESS 또는 전역 ALL의 확인 없이 부분 processlist를 전체로 간주하지 않으며 계정별 값은 null이다. 관측용 연결1개는 전체 C(t)에 포함된다. 계정 한도0은 전역 계정 제한을 따르며 양쪽0이면 추가 계정 제한 없음이지 허용0이 아니다. 계획 여유는 G-R-P, 관측 여유는 G-C(t)다. 실제 간격/누락/서버 쿼리 비원자성을 기록하며 간격보다 짧은 peak는 미관측이다.

## 결과와 준비 상태

| 결과 | 준비된 코드 | 가동 전 실제 연결 | 생성 후 실측 |
|---|---|---|---|
| 단계별 전환 | 최초 s0/append ledger·Source/operation/readback·외부 nonce 대조 | 도구 게시/리뷰·기준 Controller/보호 경로·외부 파일 전달 경로 | 실제 새 UID/Source/Image, 각 stage s0~s4·수동 지연 |
| 정상/Backend rolling | 별도 fault observer·한 번 trigger·pause/resume/recover·짝 비교 | 채택 이미지 호환·한 명 실행자/trigger argv·발생기/수집 위치·창 | 원시도 S/F/U·Room generation/수렴·Pod/Node·정상/롤링 결과 |
| Data 연결 | 기존 계산기 호출·계정 상한·TLS CLI 수집·누락/여유 산출 | 현재 DB/계정 한도 자료·기존 자격/CA/접근 호스트·수집주기 | 실제 연결수/계정별 가시성·종료 중 Pod/연결·관측 최댓값 |

실제 비용/예산/기동 지시가 먼저 연결돼야 한다. 코드/합성 성공은 실제 환경의 권한·통신·복원·업무·측정 성공이 아니다. 이 결과는 최대 수용량/성능 개선율/전체 DR를 증명하지 않는다. 앱 로직 변경은 하지 않았다.

## 기존 초기 Pool 코드의 범위

승인된 prestart의 초기 Pool 정책은 DB68/계정후보14를 요구하는 보수적인 고정 경계다. 이를 이번 DB 실제값으로 복사하지 않는다. A가 제공한 현재 한도/Process/이미지가 그 계약과 다르면 ROSA 가동 전에 해당 입력 정책/코드를 보완하고 예산을 대조해야 한다. 새 관측기의 예산/수집은 현재 값을 별도로 받는다. 고정 코드가 어떤 한도에도 자동 적응한다고 보고하지 않는다.


## 실제 기동 결과의 일괄 입력 연결

생성 전에는 execution-paths.TEMPLATE.NOT_ACCEPTED.json의 기존 실행 경로만 준비한다. 새 기동 뒤 같은 prestart work의 package와 stage-ready.json, 기존 정상 업무 근거를 bind_inputs.py에 전달한다. 이 명령은 계측 입력6개와 binding receipt를 만들며 생성/자격 파일 읽기/DB/브라우저/롤링을 실행하지 않는다. 누락 경로를 임의 생성하지 않는다.

```bash
python3 -I "$B_MEASURE/bind_inputs.py" --infra "$B_INFRA" \
  --work "$B_WORK" --stage "$B_STAGE" --business-ref "$B_NORMAL_BUSINESS_REF" \
  --prestart-config "$B_READY_CONFIG" --paths "$B_EXECUTION_PATHS" --out "$B_BOUND_OUT"
```

controller_work에는 결과가 놓일 최종 Controller 경로를 지정한다. binder를 다른 위치에서 실행했다면 결과를 해당 위치로 전달한다. 발생기에서 Controller에 연결할 기존 비대화형 argv 경로가 필요하다. null이면 미정, 빈 배열은 Controller 직접 실행을 명시적으로 선택한 경우만 쓴다. DB 두 입력의 run/scenario를 분리하고 같은 기간의 Pod/Node 원본과 연결한다. 현재 DB 전체/계정 한도·예약·종료 중 Pod는 실제 근거를 별도 결합하며 이 binder가 추정하지 않는다.

로컬52건은 기존 계측 검토 결과, 새8건은 package/Ready→계측 입력 연결 검사다. CI는 정확한 게시 Source에서 Python40/Node20을 실행한다. 실제 런타임 결과는 아직 없다. verify_source.py는 upstream 정상 어댑터/예산 검사기와 canonical LF manifest를 읽기 전용으로 대조한다.
