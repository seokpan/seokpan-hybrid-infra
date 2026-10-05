# ROSA 실제 Plan 전 로컬 준비

목적은 병합된 `terraform/rosa`를 **본인의 실행 환경에서 확인하고, 실제 입력을 수신한 뒤 첫 Plan으로 넘길 준비**를 하는 것이다. 이 문서는 기존 도구를 사용한다. 별도 입력 검사기·새 State·Cloud 실행 경로를 만들지 않는다. 원 작업은 [Infra #25](https://github.com/seokpan/seokpan-hybrid-infra/issues/25)이며 실제 실행 조건은 [입력 계약](INPUT_CONTRACT.md)·[리뷰/실행 Gate](REVIEW_AND_EXECUTION_GATES.md)를 따른다.

## 1. 본인 변경을 보존하고 Source를 대조

아래 명령은 **본인 clone의 저장소 최상위**에서 실행한다. `fetch`는 원격 참조를 갱신하며 작업 파일 수정·Branch 이동·reset·stash는 하지 않는다. 본인 작업 파일과 받아온 최신 `origin/main`을 구분한다.

```bash
git status --short --branch
git fetch origin main
git rev-parse HEAD origin/main
git diff --stat HEAD origin/main -- terraform/rosa scripts/tf-session.sh
git diff --name-status -- terraform/rosa scripts/tf-session.sh
git diff --cached --name-status -- terraform/rosa scripts/tf-session.sh
git show origin/main:terraform/rosa/versions.tf
git hash-object terraform/rosa/.terraform.lock.hcl
git rev-parse origin/main:terraform/rosa/.terraform.lock.hcl
```

#28 병합 기준은 main `eab495210981b7c5ab0db436c082f449c6554792`, Lock Blob `668098ad0f1e293982e9bcf8e931128346a00049`이다. 이후 main 변경이 있다면 최신 원본 개정과 차이를 대조한다. 본인 수정이 있으면 보존할 파일·작업 Branch·수락할 Source를 먼저 기록한다. 개인 변경을 버려 SHA를 맞추지 않는다. 두 Lock Hash가 다르면 이유를 확인하고, 입력을 받았다는 이유로 Lock을 자동 갱신하지 않는다.

## 2. Cloud에 접속하지 않고 확인

```bash
for tool in python3 bash git terraform aws rosa oc jq; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '%s: PRESENT\n' "$tool"
  else
    printf '%s: MISSING\n' "$tool"
  fi
done

python3 --version
git --version
jq --version
bash -n scripts/tf-session.sh
```

존재 여부는 버전·인증·실행 권한 확인과 다르다. Terraform이 설치돼 있다면 `CHECKPOINT_DISABLE=1 terraform version -json`으로 Core를 대조하고, AWS CLI는 `aws --version`으로 로컬 버전을 확인한다. Core **1.16.4**, Source/Lock의 AWS **6.67.0**, RHCS **1.7.7**을 유지한다. ROSA/oc CLI의 공급·지원 버전은 실행 환경에서 별도로 확정한다. 도구가 없으면 그 도구를 쓰는 검사를 `NOT RUN`으로 기록하며, 설치 성공으로 Cloud 실행 준비를 완료 처리하지 않는다.

기존 OIDC Source harness는 Python 표준 라이브러리만으로 준비할 수 있다. 아래 결과는 **격리 사본 생성/보존 확인**이며 Terraform test·실제 Operator 조회·STS 성공은 아니다.

```bash
rosa_harness_dir="$(mktemp -d)"
python3 terraform/rosa/tests/build_oidc_test_root.py \
  --source-root terraform/rosa \
  --output-root "$rosa_harness_dir"
```

생성 위치는 production Root 밖의 비어 있는 디렉터리다. builder는 OIDC/Role/Attachment·issuer 정규화와 variables/providers/versions/Lock의 보존을 검사한다. production에 넣거나 직접 production Root에서 mock 시험을 하지 않는다. 여기서는 `terraform init`, Provider 다운로드, `plan/apply`, MFA helper의 `source`, AWS/RHCS/STS 호출을 하지 않는다. 전체 Provider validate/Schema·두 mock 시험은 기존 [Source Workflow](../../.github/workflows/rosa-static-validation.yml)의 정확 커밋 결과를 연결한다.

## 3. 보호 입력은 존재·준비 상태부터 확인

A가 필요한 출력만 추출하고 B가 그 개정·Source SHA·시각·수락 범위를 확인한다. 실제 파일은 Git 밖 보호 영역에 둔다. 예시를 실행 입력으로 수락하거나 공개 댓글에 붙이지 않는다.

보호 입력이 실제 공급된 뒤에는 본인 환경에서 다음처럼 **JSON 문법과 남은 placeholder 유무만** 확인할 수 있다. 값·파일 내용을 출력하지 않는다.

```bash
rosa_inputs_file=/absolute/protected/rosa-inputs.tfvars.json
python3 - "$rosa_inputs_file" <<'PY'
import json
import sys
from pathlib import Path

try:
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    value = json.loads(text)
except (OSError, UnicodeError, json.JSONDecodeError):
    print("BLOCKED: protected input unavailable or JSON invalid")
    raise SystemExit(1)

if not isinstance(value, dict) or "INPUT_REQUIRED" in text:
    print("BLOCKED: incomplete input; not accepted for execution")
    raise SystemExit(1)

print("PASS: JSON syntax and placeholder presence only")
print("NOT RUN: HCL schema, actual resource, owner acceptance and Cloud Plan")
PY
```

이 간단한 확인은 HCL 타입/의미 검사를 대신하지 않는다. 실제 입력이 없으면 이 단계는 `BLOCKED — 공급/수신 대기`이며, 형식·값을 임의로 채워 통과시키지 않는다. 공개 기록에는 받은 개정·논리 참조·보완 담당만 남긴다. Token·비밀번호·Key·State/전체 Output·MFA 코드·환경변수 전체·OIDC JWT를 출력/수집하지 않는다.

| 입력·준비 | 공급/대조 | Source에서 소비하는 위치 | 해소되는 단계 |
| --- | --- | --- | --- |
| Account/서울 Region/VPC, Public3+ROSA Private3의 실제 ID/AZ, 개정/SHA/시각 | A #23 → B 수신 | `variables.tf`, `inputs.tf`, `cluster.tf` | 실제 첫 Plan. Data Private3은 설치 목록에 넣지 않음 |
| Classic Account Role4·Prefix/Path, 실제 Operator Policy Map·Boundary/External ID·공유 영향 | A 기반·bootstrap Owner → B | `inputs.tf`, `oidc.tf`, `cluster.tf` | 실제 조회·IAM/OIDC·Cluster 계획 |
| 서로 다른 실제 MariaDB/Redis Data SG2·Owner·기반 Rule | C #19 + A 통합 → B | `variables.tf`, 생성 후 `bindings.tf` | 첫 Plan 입력. Stage1에서도 SG2는 필수 |
| 승인 Bucket·rosa Key/Lock·목적 Role·세션/Caller | A/bootstrap Owner + B | `backend.tf`, `providers.tf`, `inputs.tf`, MFA helper | 실제 Backend/Provider 인증 및 Plan 준비 |
| 목적 조회·IAM/OIDC·SG 관리 등 서비스 권한·지원 조합 | B 필요 범위 도출 + A bootstrap 리뷰/적용 | `bootstrap/iam.tf`, rosa의 Data/Resource 블록 | Backend 권한만으로 서비스 권한 완료를 표시하지 않음 |
| 실제 stable 4.20 GA patch·Worker disk·Quota/구독·지원 | B 확인/A 리뷰 | `variables.tf`, `cluster.tf` | 실제 첫 Plan 준비 |
| 입력·지원·예비 비용·실행 창의 첫 Plan 준비 검토 참조 | B 준비/A·D 관련 검토 | `execution_review` | 첫 Plan 진입. 실제 전체 Plan의 수량/영향·총비용 리뷰는 그 뒤 생성 전 |

이미 합의된 A/C/D 파일 경계와 Registry/CI Source 병합·Registry bootstrap Apply 보고는 다시 대기로 만들지 않는다. 실제 출력·목적 권한·통합 Plan 수신은 별도 상태다. DB 데이터 이전·전체 Backup·VPN·복구 Host는 첫 ROSA Plan의 직접 입력이 아니다.

## 4. Backend·Caller는 다음 실제 인증 단계에서 확인

현재 `backend.tf`는 서울 Region, `phase2/rosa/terraform.tfstate`, 암호화와 S3 native Lock을 선언한다. Bucket은 보호 Backend 설정으로 공급한다. 이 Source를 읽은 결과는 **설정 확인**이며 S3 접근 성공이 아니다.

`scripts/tf-session.sh`는 지정 사람 IAM User와 MFA로 `seokpan-tf-rosa` 세션을 발급해 현재 셸의 환경변수에 넣는다. 이는 AWS/STS를 호출하는 실제 인증 작업이다. 로컬 문법 확인과 다르게, 지정 실행자가 실제 준비 단계에서 수행하고 현재 Caller·만료·Backend/Provider의 동일 주체를 보호 경로에서 대조한다. 이 문서의 오프라인 명령 묶음에는 포함하지 않는다.

- AWS Provider와 S3 Backend는 같은 목적 AWS 세션을 사용해야 한다. 자기 State/Lock 접근 성공과 VPC/IAM/Cluster 서비스 권한 성공은 별도로 확인한다.
- RHCS 인증은 별도 `RHCS_TOKEN` 공급이다. AWS 세션 성공으로 Red Hat 인증·구독·지원 성공을 판정하지 않는다. RHCS Token을 Terraform 변수/State에 넣지 않는다.
- 현재 Source의 rosa Role은 Backend 권한만 선언한다. B의 호출 범위표를 A 소유 bootstrap의 제한 서비스 권한 후속에 연결한다. 실제 유효 권한은 아직 조회하지 않았으며 Source 누락만으로 계정 권한 부재를 단정하지 않는다. 개인 Admin 성공을 목적 Role 성공으로 사용하지 않는다.
- MFA 코드·Access/Secret Key·Session Token·RHCS Token·JWT를 공개 로그로 수집하지 않는다. 기존 helper가 발급한 자격증명 파일을 새로 만들지 않는다.

## 5. 첫 Plan·생성·생성 후 연결을 구분

| 순서 | 다음 단계로 넘길 결과 | 아직 끝난 것으로 표시하지 않는 것 |
| --- | --- | --- |
| 로컬 준비 | 수락 Source/Lock·본인 도구·보호 입력 개정·누락/담당 정리 | 실제 Caller/Backend·지원·Cloud Plan |
| 실제 첫 전체 Plan | 실제 입력/목적 권한/지원 수락, 보호 Plan의 수량·변경·삭제/교체·기반 보존 영향 리뷰 | 유료 생성 승인 |
| 승인 생성 | 동일 Code/Lock/입력의 전체 Plan, 현재 누적+잔여+실패/정리 비용·창·범위/삭제 책임 수락 | App/Data 업무 검증 |
| 생성 후 Stage2 | 현재 Cluster·Worker Node/ENI/SG 관측 → 새 전체 Plan → Binding·허용/거부 검증 | Worker Pull·Data/Secret/Migration 업무 성공 |
| App 배포/시험 | D/A Pull/Image, C Data/TLS/Schema, B Secret/관리/GitOps·단일 Migration → 최초 수동 Sync/업무 시험 | 전체 T01~T23·Recovery·최종 정리 |

첫 Plan은 `cluster_enabled=true`, `worker_sg_binding=null`로 준비할 수 있다. 이때 Data SG2의 입력은 필수이고 Worker→Data ingress Rule은 0개다. 실제 Worker SG는 생성 후 확인한다. IAM/OIDC만 먼저 준비하는 선택은 기존 [실행 Gate](REVIEW_AND_EXECUTION_GATES.md)의 **기존 Cluster 없음 확인·false의 삭제 영향·전체 Plan·해당 실행 승인**을 따른다. false를 범용 “안전 모드”로 쓰지 않는다.

첫 Plan의 예비 비용 검토와 실제 전체 Plan 뒤 총비용/실행 승인을 구분해 순환 대기를 만들지 않는다. $450 계획선·$500 전체 한도, Owner/Shared Execution·중단/잔존 책임을 유지한다. OCP 인계/검증과 이 준비는 병행하며 OCP 삭제를 ROSA 시작 조건으로 추가하지 않는다.

## 공부·작업 기록

- **Code**는 원하는 설정, **Lock**은 선택 Provider 고정, **실제 입력**은 이번 계정/기반 개정, **Caller**는 요청한 사람의 목적 Role 세션이다. 네 가지가 같아야 같은 실행 조합을 설명할 수 있다.
- **Plan**은 그 조합과 실제 상태로 예상 변경을 계산한다. **Apply**는 검토한 변경을 서비스에 요청한다. GitHub 병합·JSON 문법·harness 생성만으로 Cluster가 생성되지는 않는다.
- 이번 작업은 원 #25에 먼저 Source/명령·범위·결과/`NOT RUN`·직접 누락·다음 담당을 기록하고, Docs #21·Tracker·05에는 원본 링크·상태·영향을 연결한다. 본인 환경 확인과 보조 작업환경 확인을 구분한다. 시험/재시험이 실제 수행되면 기존 Evidence 규칙에 따라 새 Run을 남긴다.
