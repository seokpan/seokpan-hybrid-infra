# seokpan-hybrid-infra

석판(Seokpan) 2차 프로젝트 — **B1: Cloud Primary + On-Prem Restore-based Recovery** 인프라 저장소

- 기준 문서: `seokpan-hybrid-docs` / `02_TARGET_ARCHITECTURE.md` (Source of Truth)
- AWS / ROSA 인프라: Terraform
- On-Prem 측 설정, GitOps Bootstrap: Ansible / Script
- ROSA 내부 Desired State: [seokpan-hybrid-gitops](https://github.com/seokpan/seokpan-hybrid-gitops) (OpenShift GitOps)

## Resource Ownership

| 영역 | Owner |
|---|---|
| VPC / Subnet / Route / NAT / SG, RDS, ElastiCache, ECR, Backup S3, AWS 측 Hybrid 리소스 | Terraform (`foundation`) |
| ROSA Classic Cluster, Machine Pool, Operator Role / OIDC | Terraform + RHCS (`rosa`) |
| ROSA Account-wide Role / Policy | Terraform (`foundation`) |
| Namespace / Project, Deployment, Service, Route, ConfigMap, ServiceMonitor | GitOps (`seokpan-hybrid-gitops`) |
| GitOps Operator 설치, Root Application 등록 (최초 1회) | GitOps Bootstrap (`ansible/`) |
| 실제 Secret 값 | 별도 Secret 공급체계 (IAM/Security 상세설계에서 확정) |

> 같은 리소스를 Terraform과 GitOps가 동시에 관리하지 않습니다.

## 디렉터리 구조

```
seokpan-hybrid-infra/
├── bootstrap/            # Terraform 원격 state 기반 (S3)
├── terraform/
│   ├── foundation/       # ROSA 삭제와 무관하게 유지되는 Cloud Foundation
│   └── rosa/             # 반복 생성·삭제하는 ROSA Classic Multi-AZ
└── ansible/              # On-Prem 측 설정, GitOps Bootstrap
```

## Terraform State

| state | key | 소유 리소스 | Runtime Lifecycle |
|---|---|---|---|
| bootstrap | `bootstrap/terraform.tfstate` | S3 Backend, 버저닝, 암호화, 퍼블릭 차단, 잠금, Backend 접근 정책(현재 IAM 권한으로 충족, IAM 설계 시 재검토 — #5) | Persistent |
| foundation | `foundation/terraform.tfstate` | Network, RDS, ElastiCache, ECR, Backup S3, Hybrid AWS 측, ROSA Account-wide Role | 리소스별 Persistent / Stoppable / Re-creatable |
| rosa | `rosa/terraform.tfstate` | ROSA Classic Multi-AZ, Machine Pool, Cluster Operator Role, OIDC | Ephemeral (Validation Window) |

### 생성 / 삭제 순서

- 생성: `bootstrap` → `foundation` → `rosa` → GitOps Bootstrap → (GitOps가 나머지 동기화)
- 삭제: `rosa` → (`foundation`) → (`bootstrap`)
- 일상적인 비용 절감 destroy는 **`rosa`만** 대상으로 합니다.
- `foundation`, `bootstrap` 전체 destroy는 **팀의 명시적 승인 없이 수행하지 않습니다.**
- `foundation`에 있다고 상시 실행한다는 뜻이 아닙니다. RDS Stop, NAT 재생성 등 비용 조정은 **Console이 아닌 Terraform 변수/플래그**로 관리해 Drift를 만들지 않습니다.

## backend 설정 템플릿

```hcl
terraform {
  backend "s3" {
    bucket       = "seokpan-tfstate-847835841591"
    key          = "<state명>/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
```

> Region은 현재 bootstrap 기준 `ap-northeast-2`이며, 최종 Region은 Network 상세설계에서 확정합니다.

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
- Red Hat OCM 토큰은 `RHCS_TOKEN` 환경변수로만 주입합니다.
- RDS 마스터 비밀번호는 Terraform이 값을 직접 다루지 않는 방식(`manage_master_user_password` 등)을 우선 검토합니다.
- `sensitive` 표시만으로 값이 State에서 제거되지 않습니다. State 저장 자체를 최소화합니다.
- Secret 재주입 절차가 정의되기 전에는 Clean Recreate를 PASS로 판단하지 않습니다.

## 비용 관리 (Cost Gate)

- AWS 지원 한도: **$500**
- **첫 Full Apply 전에 Cost Gate를 통과**해야 합니다. (시간당·일 Baseline, Window별 예상 비용, 최대 허용 ROSA 가동시간 산출)
- ROSA Classic Multi-AZ는 상시 유지하지 않고 **Integration / Validation Window마다 생성·삭제**합니다.
- RDS는 Persistent Data Layer이며 미사용 시 Stop합니다.
- 모든 리소스에 `default_tags`(Project, Phase, ManagedBy, Component)를 지정합니다.

## Manual PoC

- 불확실성을 줄이기 위한 Console / ROSA CLI 기반 Manual PoC를 허용합니다.
- 자동 생성 코드(Import, `-generate-config-out`)는 참고용이며 최종 IaC가 아닙니다.
- 최종 재현성은 **Terraform Clean Recreate 성공**으로 판단합니다.

## bootstrap 복구 절차

State 버킷이 유실되면 bootstrap 스택도 init할 수 없습니다.

1. `bootstrap/backend.tf`를 임시로 다른 이름으로 변경
2. `terraform init -reconfigure` (로컬 state로 전환)
3. `terraform apply`로 버킷 재생성
4. `backend.tf` 원복 후 `terraform init -migrate-state`로 state 재이전

> 버킷은 `prevent_destroy`와 버저닝으로 보호됩니다. 버킷이 유실되면 다른 state도 함께 유실되므로 버킷 삭제는 팀 합의 없이 진행하지 않습니다.