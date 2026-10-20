# ROSA 보류 복원 뒤 Cloud 앱 기동 연결

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/blob/078b93184790231fbc3b0e03c9ce0b767ab39dab/execution/TJUNG03_FULL_REPOSITORY_AUDIT_20261021.md).

이 도구는 **새 Data Pod → 목적 SQL → Schema·앱 모드 → 초기 Pool → 배포 Source → 명시 Sync → Ready 확인**을 연결한다. 실제 입력이 없으면 로컬 누락 목록만 출력한다. ROSA 재생성, Foundation/NAT/EIP/RDS 변경, Import, Migration upgrade/stamp, Redis 키 수정, 자동 Sync는 수행하지 않는다.

```mermaid
flowchart LR
  A[새 ROSA 생성과 보류 복원] --> B[새 개인 로그인]
  B --> C[새 ID와 보호 UID 연결]
  C --> D[승인 이미지의 임시 Data Pod]
  D --> E[목적 SQL 읽기]
  E --> F[Schema와 기존 게임 상태 확인]
  F --> G[초기 Pool 계산과 Source 반영]
  G --> H[Argo 전체 선언 비교와 명시 Sync]
  H --> I[Ready 확인]
  I --> J[정상 업무와 선정한 검증·측정]
```

## 준비할 파일

`prestart.TEMPLATE.NOT_ACCEPTED.json`을 Git 밖의 mode600 보호 파일로 복사한다. 실제 비밀번호·Token은 Git/채팅/CLI 인수에 넣지 않는다. 경로는 다음 파일을 가리킨다.

| 필드 | 공급하는 내용 |
|---|---|
| lifecycle_config | 기존 생성/복원 설정. 실행 전에는 새 current_cluster_id와 ready platform handoff가 있어야 한다. |
| launch_inputs | 기존 Cloud 입력. run_id·DB/Redis 주소·보호 입력 개정·리뷰 근거·비용/시작종료가 필요하다. 새 Cluster/API/Ingress/UID와 실제 Infra checkout SHA는 이 도구가 파생해 별도 결과에 저장한다. 원본 파일은 덮어쓰지 않는다. |
| final_data_handoff | 기존 최종 Data/Schema 대조와 이전 앱·직접 DB 쓰기 프로세스 중단 근거. 기존 cloud-readiness 형식을 사용한다. 기존 회원 이관을 생략할지 여부를 이 도구가 임의 변경하지 않는다. |
| schema_credentials_file | `schema-credentials.TEMPLATE.NOT_ACCEPTED.json` 형식의 기존 db_admin URL. A가 가진 기존 보호 자료에서 B가 연결한다. 새 계정/새 보고서를 요구하는 항목이 아니다. |
| runtime_inputs_file | `runtime-inputs.TEMPLATE.NOT_ACCEPTED.json`의 Secret2/CA ConfigMap2. Secret data는 기존 보호값의 base64이며 암호화 자체가 아니므로 파일 mode600 유지. A의 기존 DB 입력과 B의 기존 Redis 자료에서 B가 묶는다. 새 Namespace에 보류 복원된4개와 같으면 쓰지 않고, 다르면 앱0/수동Argo 상태에서 UID/resourceVersion 조건으로 해당 data만 갱신·대조한다. Pull Secret은 포함할 수 없다. |
| gitops_source | Cloud 고정 c75a4b5e3b3b9bae4ea7d8a431962cb9ca7308d7 checkout. 기존 13개 Git blob 검사를 재사용한다. Recovery Source와 섞지 않는다. |
| kustomize / oc | 기존 실행 파일의 절대 경로. Kustomize5.7.1·Python/PyYAML은 ROSA 가동 전에 기존 도구 준비 절차로 준비한다. |
| personal_kubeconfig | 새 OAuth callback 반영 뒤 tjung03으로 실제 로그인한 개인 파일. 복원 관리자 kubeconfig나 삭제된 Cluster Token으로 대체하지 않는다. |

ROSA 생성 전에는 개인 kubeconfig/새 ID가 없는 것이 정상이다. 기존 `rosa-recreate-resume.sh`가 생성·보류 복원을 담당한다. 그 도구의 `NEW_LOGIN_REQUIRED` 이후 새 OAuth 로그인에서 복사한 `oc login` 명령을 **Controller의 새 개인용 KUBECONFIG**로 실행한다. Token을 채팅에 보내거나 관리자 설정을 덮어쓰지 않는다. 그 파일 경로만 위 설정에 연결한다. 기존 삭제 전 로그인 시험을 반복한다는 뜻이 아니다.

## 실행 위치와 명령

Controller `jth`, 최종 검토된 고정 Infra checkout에서 실행한다. 사용자 재생성 보류가 해제되기 전에는 아래 `--execute`를 실행하지 않는다. 기존 생성 명령과 이 도구가 자동으로 서로를 호출하지 않는다.

```bash
# 공통: 개인 보호 설정 경로만 지정한다. 실제 자격값을 넣지 않는다.
set +x
set -euo pipefail
umask 077
[[ "$(id -un)" == jth ]] || { echo STOP_USE_CONTROLLER_JTH; exit 1; }
: "${B_INFRA_REPO:?최종 검토된 Infra checkout 절대 경로}"
: "${B_CLOUD_READY_CONFIG:?실제 보호 입력 경로를 채운 prestart JSON 절대 경로}"
: "${B_CLOUD_READY_WORK:?이번 실행 전용 보호 결과 디렉터리 절대 경로}"

# 지금 가능한 입력 경로 확인. API/SQL/생성/Sync 없음.
bash "$B_INFRA_REPO/scripts/cloud-ready.sh" --config "$B_CLOUD_READY_CONFIG"
```

```bash
# 실제 새 환경·최종 Data·개인 로그인 준비 뒤에만 실행한다.
# 처음 지정한 work 경로를 실패 후에도 그대로 사용한다.
bash "$B_INFRA_REPO/scripts/cloud-ready.sh" \
  --config "$B_CLOUD_READY_CONFIG" --work "$B_CLOUD_READY_WORK" --execute
```

성공 `PASS_PRESTART_TO_MANUAL_SYNC_BOUNDARY`는 Source 반영과 이번 도구의 임시 Pod 정리까지다. 실제 앱 기동 성공이 아니다. 출력의 `console_file`을 새 Argo Console에서 실행한다. 기존 `compare-and-sync.js`를 재사용하며 기본은 읽기 비교, 기동할 때만 명시적으로 `SYNC_NOW=true`와 확인 입력을 사용한다. 전체 선언/새 Application UID/Source가 일치해야 요청하고 prune=false/retry0이다. CLI로 Sync 권한을 우회하지 않는다.

```bash
# 명시 Sync 뒤 Ready 확인. SQL·Schema·Pod 생성은 다시 하지 않는다.
bash "$B_INFRA_REPO/scripts/cloud-ready.sh" \
  --config "$B_CLOUD_READY_CONFIG" --work "$B_CLOUD_READY_WORK" \
  --execute --after-sync
```

성공 `PASS_CLOUD_DEPLOYMENT_READY_FOR_BUSINESS_VERIFICATION`가 **앱을 올리고 정상 업무·검증 테스트를 시작할 시점**이다. 원본 `single-ready.json`은 `PASS_CLOUD_STAGE_READY_NOT_BUSINESS_ACCEPTANCE`이며 업무/Pool 수명·최대 성능 성공으로 확대하지 않는다.

## 각 단계의 실제 판정

| 단계 | 성공과 결과 전달 | 실패 시 동작 |
|---|---|---|
| 새 입력 | 새 platform ID/API, 개인 tjung03/TLS, Namespace와 보호4 UID, native Pull SA를 `new-inputs.json`에 연결 | 옛 Cluster/자격·임시 Pull 참조면 중단. 옛 입력을 갱신했다고 추정하지 않음 |
| 보호 입력 | 실제 공급4개와 보류 복원된4개를 비교, 같은 값은 재공급하지 않음. 이번 Namespace UID/개인 인증·앱0 확인 뒤 차이만 data patch/readback | patch 불명확 결과는 같은 UID/실제 data가 원하는 값인지 읽기 대조. 두 번째 patch·Pull 재연결 없음 |
| 임시 Pod | 승인 Backend digest, worker 전용, token/env/Secret mount 없음. 공개 CA2개만 readOnly. 새 UID를 `new-pod.json`·`sql-target.json`에 연결 | create 표식 후 응답이 끊기면 같은 이름을 읽어 소유/전체 spec/UID 대조. 없거나 다르면 재생성하지 않음 |
| 목적 SQL | 기존 읽기 probe를 그대로 사용. 두 목적 계정/TLS/상한68·계정별 후보14/표 읽기 결과를 `purpose-sql-result.json`에 저장 | 인증/권한 실패는 값·원문 대신 sanitized 실패 receipt. 성공한 SQL은 재실행하지 않음 |
| Schema | db_admin의 TLS 읽기 전용 세션으로 alembic_version=20260902_0002 확인 | 버전 차이 시 중단. upgrade/stamp/DDL/Import를 자동 실행하지 않음 |
| captured 모드 | 모든 이전 writer 중단 인계가 선행. DB 진행 중 Game0·완료 결과 모순0·Redis room family0이면 기존 회원/종료 이력을 보존한 채 신규 Game부터 captured 허용 | 기존 방/진행/미정리 task가 있거나 SCAN 상한이면 분류 필요로 중단. prefix DEL/FLUSHDB/TTL 변경/추측 이행 없음 |
| 초기 Pool | 실제 두 목적 계정 한도로 7Pod×1Process×2Engine×2=28+예약10=38/68, 계정별14와 timeout5초 선택 | 초기 설정 선택과 기동 후 반환/고갈/재접속 시험을 분리 |
| Source | 실제 위 결과로 execution-evidence.json을 생성한 뒤 기존 Application Source만 반영 | 응답 불확실이면 같은 marker/UID/실제 Source를 읽어 반영 여부만 대조. 두 번째 patch 없음 |
| Pod 정리 | 전용 Pod만 UID precondition으로 삭제, 불명확 응답 후 읽기 대조 | 다른 Pod/RBAC/Secret을 정리하지 않음. 같은 delete 반복 없음 |
| Sync/Ready | 명시 Console 요청 뒤 Source/개정/전체 inventory·정확한 Replica·Route를 기존 readback으로 확인 | 504/불명확 Sync는 재제출하지 않음. 실제 operation/Source부터 대조 |

DB/Redis의 기존 상태가 비어 있지 않으면 실제 분류·해결 결과가 필요하다. 이것을 빈 상태나 안전한 전환으로 가정하지 않는다. 실제 실행 중 발견된 모순을 자동 데이터 변경으로 해결하는 기능은 없다. 앱 기동 전 새 값 없이 구현할 수 있는 연결과, 실제 데이터 상태 판단을 구분한다.

## 중단·재개

- 성공 receipt는 같은 입력/Cluster/code hash에서 재사용한다. 다른 work 이름으로 실패를 우회하지 않는다.
- Pod create/Source patch/delete 응답 불확실은 위 읽기 대조로만 재개한다.
- SQL/Schema/Ready 읽기 단계가 실패하거나 중간에 끊겼다면 실패 receipt를 확인하고, 같은 입력의 읽기 재개에 한해 아래 옵션을 사용한다. 성공 단계는 이 옵션으로도 재실행하지 않는다.

```bash
bash "$B_INFRA_REPO/scripts/cloud-ready.sh" \
  --config "$B_CLOUD_READY_CONFIG" --work "$B_CLOUD_READY_WORK" \
  --execute --resume-read-only
# Ready 읽기만 미완료였다면 --after-sync도 추가한다.
```

- 입력/Source/개인 자격 파일 경로가 바뀌면 기존 결과를 새 대상에 자동 귀속하지 않는다. 이미 실행한 단계와 기존 Pod/Source를 대조한 뒤 새 실행 묶음을 만든다. 인증 Token 자체의 갱신은 같은 개인 파일 경로에서 가능하나 새 Cluster/주체 검사는 유지한다.
- 로컬 render가 일부만 만들어지고 끊긴 경우 incomplete package를 덮어쓰지 않는다. API 이전 단계이므로 해당 로컬 출력만 확인해 새 work로 시작할 수 있다. 이미 Pod/Source 표식이 있으면 이 방법으로 우회하지 않는다.
- 두 개의 Controller 실행은 lock으로 막는다. 최초/완료 UTC와 실패 표식은 work에 남는다.

## 2Pod·목표 단계와 결과 인계

최초1/1 Ready 뒤 실제 정상 업무 결과를 `business-evidence.TEMPLATE.NOT_ACCEPTED.json`에 연결한다. true/근거를 자동 생성하지 않는다. 다음 명령은 최초 SQL·Schema를 반복하지 않고 같은 package/기록을 사용한다.

```bash
: "${B_NORMAL_BUSINESS_EVIDENCE:?최초 단계의 실제 정상 업무 결과 JSON}"
bash "$B_INFRA_REPO/scripts/cloud-ready.sh" \
  --config "$B_CLOUD_READY_CONFIG" --work "$B_CLOUD_READY_WORK" \
  --execute --stage shared --business-evidence "$B_NORMAL_BUSINESS_EVIDENCE"
# 새로 출력된 Console로 해당 Source만 명시 Sync 후:
bash "$B_INFRA_REPO/scripts/cloud-ready.sh" \
  --config "$B_CLOUD_READY_CONFIG" --work "$B_CLOUD_READY_WORK" \
  --execute --stage shared --after-sync
```

target도 같은 명령의 stage를 target으로 바꾸고 shared의 정상 업무·다른 Pod 재접속/공유 상태 근거를 전달한다. 모든 단계 확장을 반드시 실행하라는 뜻은 아니며 발표/예산에 선정한 단계까지만 진행한다. 아래는 정상 업무 수락 이후 기존 측정 도구로 넘기는 연결이다.

```bash
: "${B_NORMAL_BUSINESS_REF:?실제 정상 업무 근거 참조}"
python3 -I "$B_INFRA_REPO/tools/cloud-launch/cloud_launch.py" measurement \
  --package "$B_CLOUD_READY_WORK/package" --stage shared \
  --readback "$B_CLOUD_READY_WORK/shared-ready.json" \
  --normal-business-ref "$B_NORMAL_BUSINESS_REF" \
  --receipt "$B_CLOUD_READY_WORK/shared-measurement-plan.json"
node "$B_INFRA_REPO/tools/cloud-measurement/run-cloud-measurement.mjs" \
  --plan "$B_CLOUD_READY_WORK/shared-measurement-plan.json"
# 실제 선정 측정 실행 때만 --execute --out <새 보호 결과 디렉터리> 추가.
```

D에게 전달할 것은 run/Cluster/Source·Release·단계·실제 Replica·앱 Origin과 ready receipt/시각이다. 알림/관측 결과와 같은 Run에 연결한다. 결과 파일에는 내부 경로가 있으므로 GitHub에는 sanitized 기술 결과만 게시한다. 실패 보고는 마지막 status/reason·work 위치·해당 실패 receipt만 전달하며 보호값/Token/원문 SQL 오류를 보내지 않는다. 명령 준비와 합성 시험은 실제 앱 기동 성공이 아니다.


## 발표 계측을 선택한 실행

발표에서 stage별 전환시간과 정상/Backend rolling 비교를 확보할 경우 [PRESENTATION_MEASUREMENT_RUN](../../docs/PRESENTATION_MEASUREMENT_RUN.md)을 따른다. 최초 stage --execute는 stage_clock.py entry로 감싼 명령 하나로 대체한다. 기존 명령을 실행한 뒤 시각을 만들기 위해 다시 실행하지 않는다. stage별 Ready/정상 업무 근거가 생기면 bind_inputs.py가 같은 package·UID·Source/Image/Replica를 정상/롤링·DB 관측 입력으로 결합한다. 실제 생성 지시는 별도이며 이 연결은 재생성을 호출하지 않는다.
