# seokpan-hybrid-infra

석판(Seokpan) 2차 프로젝트 — **B1: Cloud Primary + On-Prem Restore-based Recovery** 인프라 저장소

- 기준 문서: `02_TARGET_ARCHITECTURE.md`, `03_DETAILED_DESIGN.md` (Source of Truth)
- AWS / ROSA 인프라: Terraform
- On-Prem 측 설정, GitOps 최초 설치, Backup/Restore 실행: Ansible / Script
- ROSA 내부 Desired State: [seokpan-hybrid-gitops](https://github.com/seokpan/seokpan-hybrid-gitops) (OpenShift GitOps)

## Resource Ownership

| 영역 | Owner |
|---|---|
| State Backend, TF 실행 Role | Terraform (`bootstrap`) |
| VPC / Subnet / Route / NAT / SG, RDS, ElastiCache, ECR, Backup S3, AWS 측 Hybrid 리소스 | Terraform (`foundation`) |
| ROSA Account-wide Role / Policy | Terraform (`foundation`) |
| ROSA Classic Cluster, Machine Pool, Cluster-specific Operator Role / OIDC | Terraform + RHCS (`rosa`) |
| Namespace / Project, Deployment, Service, Route, ConfigMap, ServiceMonitor | GitOps (`seokpan-hybrid-gitops`) |
| GitOps Operator 최초 설치, Root Application 최초 등록 | Infra의 최소 Ansible Bootstrap (`ansible/`) |
| 실제 Secret 값 | 별도 Secret 공급체계 (SOPS + age, 담당자 공급) |

> 같은 리소스를 Terraform과 GitOps가 동시에 관리하지 않습니다.

## 디렉터리 구조

```
seokpan-hybrid-infra/
├── terraform/
│   ├── bootstrap/        # State Backend(S3), TF 실행 Role
│   ├── foundation/       # ROSA 삭제와 무관하게 유지되는 Cloud Foundation
│   ├── rosa/             # 반복 생성·삭제하는 ROSA Classic Multi-AZ
│   └── modules/          # 필요한 반복 구조의 Local Module (필요 시 생성)
├── ansible/              # On-Prem 구성, GitOps 최초 설치, Data Backup/Restore
└── scripts/              # 실행 주체·대상 검사, State 간 입력 추출 (필요 시 생성)
```

- 각 Root(`bootstrap`, `foundation`, `rosa`)는 독립적으로 plan/apply하며, 각자 provider 제약과 `.terraform.lock.hcl`을 가집니다.
- `modules/`는 Root의 State에 포함되는 코드 묶음이며 독립 State가 아닙니다.

## Terraform State

전용 State 버킷 하나에서 Root마다 다른 key·잠금을 사용합니다.

| Root | key | 소유 리소스 | Runtime Lifecycle |
|---|---|---|---|
| bootstrap | `phase2/bootstrap/terraform.tfstate` | S3 Backend, 버저닝, 암호화, 퍼블릭 차단, HTTPS 강제, 잠금, TF 실행 Role(예정, #10) | Persistent |
| foundation | `phase2/foundation/terraform.tfstate` | Network, RDS, ElastiCache, ECR, Backup S3, Hybrid AWS 측, ROSA Account-wide Role | 리소스별 Persistent / Stoppable / Re-creatable |
| rosa | `phase2/rosa/terraform.tfstate` | ROSA Classic Multi-AZ, Machine Pool, Cluster Operator Role, OIDC | Ephemeral (Validation Window) |

### 생성 / 삭제 순서

- 생성: `bootstrap` → `foundation` → `rosa` → GitOps Bootstrap → (GitOps가 나머지 동기화)
- 삭제: `rosa` → (`foundation`) → (`bootstrap`)
- 일상적인 비용 절감 destroy는 **`rosa`만** 대상으로 합니다.
- `foundation`, `bootstrap` 전체 destroy는 **팀의 명시적 승인 없이 수행하지 않습니다.**
- `foundation`에 있다고 상시 실행한다는 뜻이 아닙니다. RDS Stop, NAT 재생성 등 비용 조정은 **Console이 아닌 Terraform 변수/플래그**로 관리해 Drift를 만들지 않습니다.

### State 간 값 전달

- `terraform_remote_state`로 다른 Root의 State 전체를 읽는 방식은 **기본으로 사용하지 않습니다.**
- 해당 State 접근 권한이 있는 담당자가 **필요한 비밀값 아닌 Output만** 추출한 제한된 입력 파일로 전달합니다. (03 §3-F.5)
- 입력 파일은 Git 밖 보호 영역에 두고, Account/Region·출처 Revision·생성 시점을 확인해 오래된 값으로 실행하지 않습니다.

## 버전 기준

| 대상 | 버전 | 고정 방법 |
|---|---|---|
| Terraform | `1.16.4` | 각 Root `required_version = "1.16.4"`, controller 서버 공용 설치 |
| AWS provider | `6.67.0` | 각 Root `version = "6.67.0"` + `.terraform.lock.hcl` 커밋 |

- AWS provider는 03 초기 후보(6.66.0) 대신 **6.67.0을 채택**했습니다. bootstrap을 6.67.0으로 apply·검증했기 때문입니다. (#10 변경 기록)
- 새 버전을 자동 채택하지 않습니다. 버전 변경(`terraform init -upgrade`)은 PR로 리뷰합니다.

## backend 설정 템플릿

새 Root를 만들 때 `backend.tf`에 아래를 넣고 `key`만 바꿉니다.

```hcl
terraform {
  backend "s3" {
    bucket       = "seokpan-tfstate-847835841591"
    key          = "phase2/<root명>/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
```

> Region은 서울 `ap-northeast-2`로 확정되었습니다. (03 §3-B.3)

## 기존 clone 사용자 안내 (2026-10-01 구조 변경)

`bootstrap/`이 `terraform/bootstrap/`으로, state key가 `phase2/bootstrap/`으로 바뀌었습니다. 이전에 clone한 경우 한 번 실행합니다.

1. `git checkout main && git pull`
2. `rm -rf bootstrap` (예전 폴더의 `.terraform/` 잔여물 정리)
3. `cd terraform/bootstrap && terraform init -reconfigure`
4. `terraform plan` 결과가 `No changes`인지 확인

## 작업 흐름

```
Issue → Branch → terraform fmt → validate → plan → PR Review → Approved Apply
```

1. GitHub 웹에서 Issue 등록
2. `main` 최신화 후 브랜치 생성: `feature/<이슈번호>-<작업명>`
3. `fmt` / `validate` / `plan` 후 PR 생성 (plan 결과 첨부)
4. 팀원 1명 승인 → **Squash and merge**
5. **지정된 실행 주체**가 `main` 기준으로 다시 plan 후 apply

### 규칙

- apply / destroy는 **지정된 실행 주체만** 수행합니다. (스택별 실행자는 WBS에서 지정)
- apply는 `main` 코드로만 실행합니다. (예외: bootstrap 최초 구성)
- State 잠금은 충돌 방지 장치이며, 동시 apply를 허용한다는 의미가 아닙니다.
- apply 전 plan의 `destroy` / `replace` 항목을 반드시 확인합니다.
- `root`로 Terraform을 실행하지 않습니다. 각자 `su - 본인계정` 후 본인 IAM으로 실행합니다.
- Provider / Module 버전은 검증한 버전으로 고정하고 `.terraform.lock.hcl`을 커밋합니다.
- `*.tfstate`, `*.tfvars`, `.terraform/`은 커밋하지 않습니다.
- plan 파일은 `<작업명>.tfplan`으로 저장하고, **apply 후 즉시 삭제**합니다.
- 리뷰 후속 소규모 수정(문서·주석·설정 정합성)은 Issue 없이 `docs/<작업명>` 브랜치로 진행할 수 있으며, PR 본문에 원 PR과 관련 Issue를 참조합니다.
- 인프라 변경이 포함되면 반드시 Issue를 먼저 등록합니다.

## Secret 규칙

- Secret 평문을 Git, Terraform 코드, `.tfvars`, CI 로그, 발표자료에 저장하지 않습니다.
- Secret 공급 원본은 Git 밖의 SOPS + age 암호화 묶음이며, 암호문도 저장소에 넣지 않습니다.
- Red Hat OCM 토큰은 `RHCS_TOKEN` 환경변수로만 주입합니다.
- RDS 마스터 비밀번호는 Terraform이 값을 직접 다루지 않는 방식(`manage_master_user_password` 등)을 우선 검토합니다.
- `sensitive` 표시만으로 값이 State에서 제거되지 않습니다. State 저장 자체를 최소화합니다.
- Secret 재주입 절차가 정의되기 전에는 Clean Recreate를 PASS로 판단하지 않습니다.

## 비용 관리 (Cost Gate)

- AWS 지원 한도: **$500** (계획선 $450 + 여유 $50)
- **첫 Full Apply 전에 Cost Gate를 통과**해야 합니다. (시간당·일 Baseline, Window별 예상 비용, 최대 허용 ROSA 가동시간 산출)
- ROSA Classic Multi-AZ는 상시 유지하지 않고 **Integration / Validation Window마다 생성·삭제**합니다.
- RDS는 Persistent Data Layer이며 미사용 시 Stop합니다.
- 모든 리소스에 `default_tags`(Project, Phase, ManagedBy, Component)를 지정합니다.

## Manual PoC

- 불확실성을 줄이기 위한 Console / ROSA CLI 기반 Manual PoC를 허용합니다.
- 자동 생성 코드(Import, `-generate-config-out`)는 참고용이며 최종 IaC가 아닙니다.
- 최종 재현성은 **Terraform Clean Recreate 성공**으로 판단합니다.

## bootstrap 복구 절차

State 버킷이 유실되면 bootstrap Root도 init할 수 없습니다.

1. `terraform/bootstrap/backend.tf`를 임시로 다른 이름으로 변경
2. `terraform init -reconfigure` (로컬 state로 전환)
3. `terraform apply`로 버킷 재생성
4. `backend.tf` 원복 후 `terraform init -migrate-state`로 state 재이전

> 버킷은 `prevent_destroy`와 버저닝으로 보호됩니다. 버킷이 유실되면 다른 state도 함께 유실되므로 버킷 삭제는 팀 합의 없이 진행하지 않습니다.
