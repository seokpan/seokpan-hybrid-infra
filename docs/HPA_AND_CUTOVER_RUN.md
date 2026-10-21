# 별도 HPA 부하와 원본 중지·Dump GO 실행 연결

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/pull/140).

## 범위와 실행 위치

이 문서는 준비된 코드의 실제 입력·명령·판정·재개를 연결한다. 문서 작성이나 PR 게시가 실제 HPA/부하/원본 중지/ROSA 생성 지시를 대신하지 않는다. 재생성은 보류다. #85의 기동/복원과 #89의 고정 Replica 정상/롤링 계측은 변경하지 않는다.

| 도구 | 실행 위치 | 하는 일 |
|---|---|---|
| tools/hpa-load/control.py, run-controller.py | 새 ROSA 개인 Context가 있는 Controller jth | 임시 HPA·Argo Replica 예외, 관측·중단·UID 원복 |
| tools/hpa-load/load.mjs | 기존 Node가 있는 외부 발생기/Windows PC | 0→10→50→100→200→300→100→0 VU의35분 API 부하 |
| tools/hpa-load/db_probe.py | 기존 RDS TLS 접근 호스트 | 기존 MariaDB 보호 파일·CA로 현재 연결수/한도를 조회 |
| tools/hpa-load/prometheus.py | 새 ROSA 개인 Context가 있는 Controller | 기존 개인 Token을 메모리에서만 사용해 실제 Prometheus query 원본 보존 |
| tools/cutover-run | 기존 원본 Kubernetes Context·계약 파일이 실제 존재하는 위치(기존 cp-01 경로 등) | 외부 Writer 통제·#156 반영/필요 Sync·#79 확인·GO 생성·전환 판단 기록 |

HPA용 Node는 기본 https와 표준 모듈만 쓴다. k6/JMeter/Locust·브라우저 설치는 필요 없다. Python은 표준 라이브러리만 쓴다. 플랫폼·원본 자격을 다른 host로 임의 복사하지 않는다. 기존 비대화형 SSH/도구 경로로 호출하거나 자격이 있는 host에서 실행한다. 실제 그 경로가 없으면 실행 연결 입력으로 남기며 연결됐다고 간주하지 않는다.

## A. HPA 시험을 기존 정상/롤링과 분리

기존 정상/Backend 롤링 결과를 먼저 보존하거나 해당 비교를 이번 가동에서 제외한 명시적 근거를 기록한 뒤 시작한다. 두 시험은 동시에 하지 않는다. 새 HPA가 관리할 대상은 Backend 하나다. FE 이미지·template·기본 Replica와 Pod UID는 유지한다.

### 부하 의미와 제한

- 검토한 App Source `c2dad254cc407431092691f9ea5892f8324f1520`의 Guest 발급/종료, 방 목록, 랭킹 API를 사용한다.
- 최대300개의 이번 Run Guest 세션을 발급한 뒤 각 VU가 `GET /api/v1/rooms`와 `GET /api/v1/rankings?offset=0&limit=20`을 교대로 호출한다. 각 VU는 직렬 요청·1초 주기이며 느린 응답이면 요청률이 내려간다. VU를 동시 요청수나 브라우저/WSS 연결수로 표현하지 않는다.
- Guest 발급/정리는 별도 setup/cleanup이다. Member 계정·게임 방·투표를 만들지 않는다. 이 부하의 결과를 전체 게임 처리량·WSS 수용량으로 확대하지 않는다. 해당 업무·Room WSS 영향은 기존 #89 시험으로 측정한다.
- 부하 단계는 D 초안의120/180/300/300/300/120/180/600초를 구현했다.35분은 단계 시간 합계이며 setup·HPA 기동·초기 metric 대기·cleanup/원복·전체 ROSA 구축시간을 포함하지 않는다.
- VU 증가로 CPU60% 도달·HPA 확장을 보장하지 않는다. 실제 미확장을 미관측/미충족으로 남긴다. CPU 작업을 인위적으로 추가하거나 앱 코드를 고쳐 결과를 만들지 않는다.

### 생성 전 준비와 실제 입력

`tools/hpa-load/config.TEMPLATE.NOT_ACCEPTED.json`을 보호 위치에 복사한다. 새 API/Origin/UID·현재 Source hash·이미지는 생성 후 같은 채택 앱에서 결합한다. `capacity`는 현재 전체/계정 한도·예약·Pod/Process/Engine Pool과 노드 여유의 기존 실제 근거로 B가 채운다. max10은 후보이며 입력을 채웠다고 자동으로 허용하지 않는다. max+surge+terminating이 노드 상한·전체/계정 연결 예산에 들어와야 한다. 종료 중 allowance를 모르면0으로 넣지 않는다.

`db_probe_argv`는 기존 DB host에서 아래 한 번의 읽기 전용 probe를 호출할 argv다. `generator_guard_argv`는 발생기에서 Controller의 guard를 호출할 argv다. shell 문자열이 아니라 인수 배열이다. 기존 비대화형 접근/host trust만 쓴다. 자격값을 인수에 넣지 않는다.

```bash
# 기존 DB 접근 host. 동일 config를 보호 경로에 전달하며 자격 파일 자체는 이동하지 않는다.
python3 -I "$B_INFRA/tools/hpa-load/db_probe.py" --config "$B_HPA_CONFIG" --execute
# 발생기의 guard argv가 부를 Controller 명령.
python3 -I "$B_INFRA/tools/hpa-load/control.py" guard \
  --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
```

### Controller의 실행 진입점

아래는 실제 시작 지시를 받은 뒤 사용하는 명령이다. `--execute` 없는 기본 명령은 로컬 입력 검증만 한다. 같은 Run/work를 유지하며 변경 결과가 불명일 때 새 work로 우회하지 않는다.

```bash
HPA="$B_INFRA/tools/hpa-load"
# 기본 검사: API/SQL/생성 없음. 누락된 실제 입력의 경로만 출력한다.
python3 -I "$HPA/run-controller.py" plan --config "$B_HPA_CONFIG" --work "$B_HPA_WORK"
# 처음 이 시험을 실행할 때의 Source/UID/Ready/이미지·DB 현재값을 확인해 계획 저장.
python3 -I "$HPA/run-controller.py" plan --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
# 이 Run의 HPA와 임시 Replica 예외 생성, 최대45분 관측기 시작. 부하 자체는 아직0.
python3 -I "$HPA/run-controller.py" activate --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
```

HPA의 실제 min은 config 기준이며 초기 Pod 수가 항상2라는 가정은 하지 않는다. 원래 Pod 수와 config가 범위에 맞아야 한다. Argo 자동 Sync/진행 중 operation·다른 Backend HPA가 있으면 차단한다. 임시로 Backend `/spec/replicas`만 ignoreDifferences·RespectIgnoreDifferences에 결합하고 수동 Application의 나머지 spec/Source는 보존한다. 이미지·롤링 전략/template·FE Pod 변경은 관측 중단 사유다.

`plan.json`을 기존 발생기에 전달한다. 비밀번호·Cookie·Token은 포함되지 않는다. config에도 비밀번호를 넣지 않는다. API값과 내부 경로가 있는 파일은 GitHub에 게시하지 않는다.

```powershell
# 기존 Windows 발생기. 같은 고정 Source의 load.mjs와 기존 Node 경로 사용.
& $B_Node "$B_Infra\tools\hpa-load\load.mjs" --plan $B_HpaPlan
# 실제 선정 시험 실행 시에만 아래 --execute 명령을 사용한다. 신규 결과 폴더 필요.
& $B_Node "$B_Infra\tools\hpa-load\load.mjs" --plan $B_HpaPlan `
  --out $B_HpaLoadOut --abort-file $B_HpaAbortFile --execute
```

발생기는 처음 HPA metric 준비를 최대120초 기다린다. 이후 guard가45초 넘게 낡거나 Source/UID·현재 DB 전체/계정 여유·Pod 상태가 맞지 않으면 새 요청을 멈춘다. HTTP401/403 또는 최근 최대100건(최소20건)에서 비정상/잘못된 응답이5%를 넘으면 부하를 중단한다. 이는 보호용 중단 기준이며 D의 구간별5xx1% 수락 기준과 구분한다. 기본 TLS 검증·요청당5초 절대 timeout·최대1MiB 응답 제한을 유지한다.

### 중단·불명확 결과·원복

```bash
# Controller에서 중단: guard에 즉시 STOP, 소유 HPA의 증감만 Disabled.
python3 -I "$HPA/run-controller.py" stop --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
# 생성/patch 결과가 불명일 때 먼저 읽기 전용 대조. 자동 재생성/삭제 안 함.
python3 -I "$HPA/control.py" reconcile --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
```

발생기에서는 지정한 abort 파일을 생성해 중단할 수 있다. 정상/중단 모두 진행 중 요청을 끝낸 뒤 소유 Guest 세션만 종료를 시도하고 `receipt.json`을 남긴다. 세션 정리는 최대60초와 마지막 요청 timeout까지로 한정하며 미확인 수량을 보존한다. 세션 키는 메모리에만 있고 raw/결과/로그에 출력하지 않는다. 결과 불명 Guest 발급을 자동 재시도하지 않는다. 정리되지 않은 Guest 세션은 알려진 만료/인계 범위로 남기며 모두 삭제됐다고 쓰지 않는다.

발생기의 `receipt.json`을 Controller 보호 위치로 전달한 뒤 아래를 실행한다. 원본 raw의 네트워크 경로나 자격을 GitHub에 올리지 않는다.

```bash
python3 -I "$HPA/run-controller.py" finish --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" \
  --load-receipt "$B_HPA_LOAD_RECEIPT" --execute
# 실제 Replica/observedGeneration/Ready·임시 HPA 부재·원래 Application spec 확인.
python3 -I "$HPA/control.py" verify-restore --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
```

원복은 API의 UID precondition으로 이번 HPA만 삭제하고 원래 Replica·Application spec을 복원한다. 다른 HPA/UID/spec이 나타나면 덮어쓰지 않는다. 원복 요청 성공과 실제 Ready 수락을 분리한다. 응답 불명은 먼저 reconcile/verify-restore로 상태를 보고, 필요한 잔여 단계에만 `--resume-reviewed`를 명시한다. 프로세스 PID 기록만 보고 임의 kill하지 않는다.

부하를 한 번도 시작하지 않은 부분 생성의 원복은 B가 사실을 확인한 경우에만 `control.py restore ... --confirm-no-load-started --execute`를 사용한다. 실제 발생기가 돌고 있다면 이 옵션을 쓰지 않는다. 원래 Argo spec까지 이미 복원됐다면 restore를 다시 제출하지 말고 verify-restore로 판정한다.

### 결과 수집과 성공 판정

자동 보존: 단계별 시작/종료, 요청별 HTTP/JSON 유효성·latency·분모·재시도 없음, p95/p99/RPS, 발생기 CPU/메모리/event-loop, HPA 현재/목표·조건/사건 시각, Pod UID/Node/Ready/재시작·CPU/메모리, DB 전체/계정 연결수·여유, 원복 receipt. 네트워크/Route별 원본은 실제 label·수신 경로를 D 기존 query로 연결한 Prometheus 수집기가 추가 보존한다. 기본 모니터링 설치/smoke는 반복하지 않는다.

```bash
python3 -I "$HPA/results.py" --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" \
  --load-out "$B_HPA_LOAD_OUT_ON_THIS_HOST" --out "$B_NEW_HPA_SUMMARY"
```

같은 Run/UID/config·파일 checksum과 실제 원복 receipt를 필수 대조한다. 요청이 있는 모든 단계인1~6(10/50/100/200/300/100 VU) 각각에서 N>0 및5xx 비율<1%를 요구한다. 최대300 VU와 복귀100 VU도 같은 기준이며 관측만 하는 예외가 아니다. 어느 단계든 누락·표본0·NoData·기준미달이면 전체는 PARTIAL/exit2다. 0 VU인0/7단계는 이 요청오류 기준에서 제외한다.

결과의 all_nonzero_phases_http_5xx_below_1_percent는6개 단계의 전체 판정이며 phase_acceptance에는 단계별 phase/vus/N/오류율/판정이 남는다. 과거1~4단계만 가리키던 필드는 사용하지 않는다. 실제 확장/축소·표본/누락도 별도로 보존한다. 요청0/NoData를 오류0으로 바꾸지 않는다. 플랫폼과 발생기 UTC는 사건 대응용으로 보존하며 서로 다른 호스트 시각을 빼서 정확한 확장 지연을 만들지 않는다. CPU가 목표에 닿지 않았다고 가짜 성공이나 최대 수용량을 만들지 않는다.

## B. 원본 쓰기 중단과 Data 최종 Dump GO도 사전 준비

원본 중지/GO의 준비와 실제 중지/전달은 별개다. 도구는 기존 #79 verifier를 해시로 고정해 재사용하고 #156의 같은 head/manifest를 확인한다. 본문 작성 시 #156은 open·Ready for review·미병합, reviewer ggbun2 요청 상태다. 실제 head 승인/체크는 중지 실행 때 다시 대조한다. 새 기능 구현을 기다리지 않고 지금 리뷰를 진행할 수 있는 상태이며 병합은 원본 중지 시각까지 보류한다.

### 실제 입력과 기존 근거 변환

`tools/cutover-run/config.TEMPLATE.NOT_ACCEPTED.json`에 현재 원본 API·기존 Context를 포함한 source-read/contract 위치, KST로 합의한 시각(시간대 포함 ISO), 외부 쓰기 중단/회전 결과 보호 경로를 채운다. Context 이름/인증 주체를 Recovery나 과거 해시만으로 추정하지 않는다. access가 현재 인증 주체·API·원본 Deployment/Root/Child UID를 읽어 확인한다.

`writers.TEMPLATE`는 외부 진입·CI·직접DB/수동·예약/다른 서비스 네 범주의 기존 통제 명령 또는 실제 없음/중단 근거를 연결한다. **B가 기존 자료·실제 실행 출력으로 변환한다. A/D에게 새 양식을 만들라고 요청하지 않는다.** command 모드는 argv와 확인 명령을 실행하고, 수동 범위는300초 이내의 운영자 근거를 사용한다. 미확인 Writer를 없음으로 간주하지 않는다. 이 도구는 모르는 CI/DB 경로를 추측하거나 임의 SQL/권한 변경을 만들지 않는다.

`rotation.TEMPLATE`는 A의 실제 `rot_check 7200` 결과·확인 시각·기존 출력 참조·그날의 안전 종료 시각을 B가 결합한다.1800초 검사는 관리 접속 파일 준비에 유지하고7200초 확인을 대신하지 않는다. 자동 회전을 끄거나 오래된 관리 비밀번호로 우회하지 않는다. 실제 원본 중지 직전 현재 상태가 필요하며 과거 완료 검사의 반복과 다르다.

원본 중지/GO에는 새 ROSA의 기존 platform handoff(state ready/새 Cluster ID)와 채택 배포 리뷰·새 Worker/Data 연결 준비·A 사전 준비 근거도 연결한다. 해당 파일은 생성/복원 흐름에서 얻는다. 아직 없으면 원본을 멈추지 않는다. 최종 Import나 앱 업무 성공을 Dump 전 조건으로 요구하는 순환 조건은 만들지 않는다.

### 원본 조작 명령

아래 `CUT` 도구와 원본 kubeconfig/Context가 있는 host에서 실행한다. 자동 Git 병합 경로는 기존 `github_api_argv`(예: 보호 인증이 준비된 gh api 또는 기존 Controller 호출 argv)를 사용한다. 없으면 B가 합의 시각에 기존 GitHub 화면에서 병합하고 `stop --adopt-existing-merge`로 같은 PR/head·현재 창 안의 최근 병합을 검증한다. 사후에 외부 통제/회전을 수락한 것처럼 소급 기록할 수 없다.

```bash
CUT="$B_INFRA/tools/cutover-run"
# 기본 입력 검증: 실제 중지/GO 없음.
python3 -I "$CUT/cutover.py" prepare --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA"
# 실행 파일/순서만 저장. API 조회/중지/GO 없음.
python3 -I "$CUT/cutover.py" prepare --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# 실제 현재 원본 접근은 실행 전 아래 읽기로 확인. 옛 로그인 시험 반복 아님.
python3 -I "$CUT/cutover.py" access --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# 합의한 원본 중지 시각에만: 기존 외부 Writer 통제/확인 또는 없음 근거를 결합.
python3 -I "$CUT/writers.py" --plan "$B_WRITER_PLAN" --work "$B_WRITER_WORK" --execute
```

`external-hold.current.json`을 cutover config의 external_hold 경로로 사용한다. 과거 버전도 보존한다. Writer 변경 결과가 불명이면 `writers.py ... --verify-only --execute`로 현재 확인만 수행한다. 이미 시도한 변경은 자동 재제출하지 않는다.

```bash
# 현재 외부 통제·A회전·실행창·원본 접근·#156 승인/체크를 확인한 뒤에만 병합.
python3 -I "$CUT/cutover.py" stop --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# 기존 자동 Sync가 진행 중이면 관측만. 필요할 때 child에 같은 merge의 Replica0 Sync 1회.
python3 -I "$CUT/cutover.py" sync --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# 이번 실제 중지를 #79의 기존 코드로 확인. 완료 receipt가 있으면 다시 검사하지 않음.
python3 -I "$CUT/cutover.py" verify --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# 지금도 멈춰 있는지·외부통제/회전의 유효성을 대조하고 GO를 1회 생성.
python3 -I "$CUT/cutover.py" go --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
```

GitHub 화면에서 이미 병합한 경우 stop 명령에만 `--adopt-existing-merge`를 추가한다. 같은 head·현재 창·5분 이내 실제 병합을 공개 API로 검증하고 변경을 재시도하지 않는다. PR156이 리뷰 전이거나 head가 달라졌으면 실행이 막힌다. Source Sync 결과 불명은 sync 표식/현재 operation을 관측하며 중복 Sync를 제출하지 않는다.

GO 생성 성공은 `READY_TO_RELAY_DATA_FINAL_DUMP_GO`, 출력 파일은 GO.json이다. **B가 그 message를 A에게 실제 전달한 뒤 A가 기존 #44의 복제 확인·최종 Dump/Import·계정/Grant·Schema/비교를 진행한다.** 도구는 메시지를 자동 발송하지 않는다. GO에는 같은 Run/merge/근거 해시·유효 종료·남은 원본 중지 예산이 들어간다.75분 중지/120분 공동 시간은 초기 예산이지 완료시간 보장이 아니다.

원본 중지나 검사 실패 때는 STOP/HOLD를 전달하고 원본을 임의 재개하지 않는다. 원본 중지 후 새 Cloud 시험 쓰기가 생겼으면 단순 원본 재개를 차단한다. A 데이터 판정과 B 목적SQL/current·업무 근거를 결합한 명시 전환/복귀 결정만 기록한다.

```bash
python3 -I "$CUT/cutover.py" decision --choice HOLD \
  --data-decision "$B_DATA_DECISION" --config "$B_CUTOVER_CONFIG" --work "$B_CUTOVER_WORK" --infra "$B_INFRA" --execute
# CLOUD/SOURCE 선택은 실제 조건·명시 결정 뒤 사용한다. 자동 Source 재개/DB rollback 없음.
```

## 실행 전까지 남는 실제 값

1. 공통: 사용자 시작 지시, 현재 비용/가동·정리 종료 시각, 새 플랫폼/개인 Context·UID/Source·실제 업무 결과.
2. HPA: 현재 노드/Pod·Process/Pool·DB 전체/계정 한도/예약 근거, 기존 발생기 Node 경로·Controller guard 호출·DB probe host/보호 파일 경로, D 기존 metric/query·수집 경로. min/max는 이 근거로 채택한다. 스크립트/종속 도구 설치 구현을 D에게 다시 요구하지 않는다.
3. 원본/GO: 실제 원본 Context/API·계약 파일, 외부 Writer 목록/기존 통제 명령 또는 현재 없음/중단 근거, A 현재 회전 확인·이관 가능 시각. 최종 Data 결과는 GO 뒤 실제 실행에서 생긴다.

새 RDS/Valkey/NAT/EIP/노드 비용 자원을 이 도구가 만들지 않는다. HPA는 기존 노드의 Pod만 조절한다. 필요 자원이 부족하면 자동 노드 확장/예산 증액 대신 중단/부분 판정으로 남긴다. 기존 설치/Pull/TLS/UI/smoke/정상롤링·Snapshot/해독 검사는 이번 준비에서 반복하지 않았다. 새 준비 코드의 합성 검증과 실제 실행 성공은 별도다.

기존 MachineAutoscaler의 min/max가 다른 동적 노드 확장이 설정돼 있으면 계획/관측을 차단한다. HPA 때문에 기존 노드 확장기가 추가 과금 노드를 만들지 않도록 하기 위한 읽기 전용 확인이며 이 도구가 MachinePool 설정을 자동 변경하지 않는다. 해당 조회 권한/실제 고정 여부는 새 환경에서 확인한다.
