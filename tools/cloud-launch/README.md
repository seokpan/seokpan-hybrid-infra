# Cloud 앱 기동·측정 실행 계약

> **B 전수 대조 후속 — 2026-10-21:** B ROSA와 전용 잔존 자원·Snapshot4개 정리는 완료했다. 아래는 종료된 실행의 도구 계약이며 현재 재생성·Sync·SQL·부하 요청이 아니다. 실제 결과는 [앱·정상 시험](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_RUNTIME_AND_PROJECT_CLOSEOUT_20261020.md), [HPA 분석](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md), [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)를 따른다.

## 범위와 고정 소스

ROSA 생성·복원과 분리된 앱 Source 준비 도구다. Argo inline Kustomize Source와 전체 객체 선언을 준비하고 실제 Data·목적 계정·Schema 근거를 받은 뒤 Application Source를 연결한다. Source 준비·명시 Sync·Ready·업무 성공을 구분한다.

- GitOps 고정 개정은 `c75a4b5e3b3b9bae4ea7d8a431962cb9ca7308d7`이다. 기본 계약10개와 추가3개 blob을 대조한다.
- `single/shared/target`의 Backend/Frontend 수는1/1·2/1·3/3이다. 객체 수는8/8/10이며 target은 PDB와 분산 정책을 포함한다.
- Runtime ServiceAccount는 `seokpan-cloud-runtime`이다. 과거 임시 Pull 참조를 다시 붙이지 않는다.
- 새 ingress에서 같은 Host로 FE/API/WS를 연결한다. Recovery Host는 Cloud 준비의 공통 선행 조건이 아니다.
- Release COMPLETE는 입력·선언 일관성 판정이다. 실제 업무·성능 성공이 아니다.
- 준비 Pool은2, overflow0, timeout5초이며 WS timeout은5분이다. 준비 후보·실제 채택·실측을 구분한다.
- 이번 실행은 `fresh_test`다. Import·원본 쓰기 중지·VPN은 별도 이관 경로의 조건이다. 목적 계정·TLS·Schema 조건은 유지한다. [선택한 경로](../../docs/FRESH_TEST_CLOUD_RUN.md).

## 환경과 입력

당시 Controller의 기존 Infra checkout, Python3·PyYAML6.0.2·Kustomize5.7.1과 개인 kubeconfig를 사용했다. ROSA 생성·복원 진입점은 `scripts/rosa-recreate-resume.sh`다. 실제 입력·Token·보호 결과물은 Git 밖에 둔다. 아래 경로는 형식 예시이며 현재 실행 요청이 아니다.

## 1. 기존 입력 위치 조회

기존 설정과 파일 위치만 읽는다. ROSA·AWS·SQL 검사는 실행하지 않는다.

```bash
python3 -I tools/cloud-launch/cloud_launch.py inspect \
  --config /private/rosa-lifecycle.json \
  --receipt /private/new-controller-input-index.json
```

## 2. 비용·가동시간 대조

`launch-inputs.TEMPLATE.NOT_ACCEPTED.json`을 별도 보호 위치에 복사해 실제 근거를 연결한다. 과거 안내 금액·계획액은 최신 청구 확정값이 아니다. 누락 비용·시간을0으로 바꾸지 않는다.

```bash
python3 -I tools/cloud-launch/cloud_launch.py budget \
  --inputs /private/launch-inputs.json --receipt /private/new-budget-estimate.json
```

기준금액·미반영 비용·시간당 추정과 사용/정리 시간을 합산해450달러 계획선을 대조한다.500달러 한도를 올리지 않는다. 추정 통과는 실시간 청구 보장·자동 종료·실제 종료 완료가 아니다.

## 3. 고정 Source로 패키지 준비

```bash
python3 -I tools/cloud-launch/cloud_launch.py prepare \
  --source /path/to/pinned-hybrid-gitops \
  --inputs /private/launch-inputs.json \
  --kustomize /path/to/kustomize \
  --out /private/new-launch-package
```

각 단계의 `source.json`·`desired.json`·`release.json`과 `package.json`을 만든다. 실제 비밀번호·CA·Token을 읽거나 출력하지 않는다. 원본 blob이 다르면 최신 파일로 자동 대체하지 않는다.

## 4. 실제 Data 근거와 Source 연결

`execution-evidence.TEMPLATE.NOT_ACCEPTED.json`에 기존 결과의 경로를 연결한다. `fresh_test`는 현재 빈 Schema 관측·목적 계정/TLS·Schema 준비·앱 모드를 확인한다. 별도 이관 경로는 최종 Import/비교 근거를 요구한다. Migration current `20260902_0002`와 Pool·상태 전환 근거를 연결하며 완료한 SQL·upgrade를 반복하지 않는다.

```bash
# 기본 호출은 로컬 근거와 선언을 대조한다.
python3 -I tools/cloud-launch/cloud_launch.py stage \
  --package /private/new-launch-package --stage single \
  --evidence /private/execution-evidence.json \
  --kubeconfig /private/current-personal-kubeconfig \
  --receipt /private/new-stage-single.json
```

당시 별도 허가된 Source 연결에만 `--execute`를 사용했다. API·Namespace·보호 객체 UID·개인 계정과 Application을 대조한 뒤 UID/resourceVersion/spec 조건부 패치를 수행한다. Source 연결은 Sync·앱 기동이 아니다. 결과 불명·timeout에서 변경 명령을 자동 재시도하지 않는다.

## 5. Argo 명시 비교와 Sync

stage receipt의 `console_file`을 해당 Argo 브라우저 Console에서 사용한다. `SYNC_NOW = false`는 GET 비교만 수행한다. 당시 실제 기동을 선택한 경우에만 `true`와 확인창 승인을 사용했다. Source·전체 객체가 일치해야 지정8개 또는10개만 Sync하며 prune=false, retry0을 유지한다. 제출 성공은 Ready나 외부 업무 성공이 아니다.

## 6. Ready와 측정 입력

```bash
python3 -I tools/cloud-launch/cloud_launch.py readback \
  --package /private/new-launch-package --stage single \
  --kubeconfig /private/current-personal-kubeconfig --execute \
  --receipt /private/new-single-ready.json
```

같은 Source·객체·Synced/Healthy·이미지/설정·Replica·Route Admitted를 대조한다. `PASS_CLOUD_STAGE_READY_NOT_BUSINESS_ACCEPTANCE`는 외부 업무 성공과 다르다.

```bash
python3 -I tools/cloud-launch/cloud_launch.py measurement \
  --package /private/new-launch-package --stage single \
  --readback /private/new-single-ready.json \
  --normal-business-ref RUN_ACTUAL_NORMAL_BUSINESS \
  --receipt /private/new-measurement-plan.json

node tools/cloud-measurement/run-cloud-measurement.mjs \
  --plan /private/new-measurement-plan.json
```

정상6클라이언트·준비60초·관측180초와 별도 롤링/HPA 분모를 합치지 않는다. 최초 실행 시각을 놓쳤다면 나중에 계산으로 복원하지 않는다. 실행은 종료됐으며 명령 예시를 재실행하지 않는다.

## 문서 정정 범위

최초 PR85에서 저장된 깨진 한글을 현재 코드·입력 계약에 맞는 UTF-8 한국어로 다시 작성했다. 실행 코드·고정 Source·이미지·기존 성공/실패 기록은 변경하지 않았다. 원본 바이트는 Git 이력에 남는다.
