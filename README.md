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
└── scripts/              # 실행 세션(tf-session.sh), 실행 주체·대상 검사, State 간 입력 추출
```

- 각 Root(`bootstrap`, `foundation`, `rosa`)는 독립적으로 plan/apply하며, 각자 provider 제약과 `.terraform.lock.hcl`을 가집니다.
- `modules/`는 Root의 State에 포함되는 코드 묶음이며 독립 State가 아닙니다.

## Terraform State

전용 State 버킷 하나에서 Root마다 다른 key·잠금을 사용합니다.

| Root | key | 소유 리소스 | Runtime Lifecycle |
|---|---|---|---|
| bootstrap | `phase2/bootstrap/terraform.tfstate` | S3 Backend, 버저닝, 암호화, 퍼블릭 차단, HTTPS 강제, 잠금, TF 실행 Role(`seokpan-tf-*`) | Persistent |
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

## 인증 (MFA + TF 실행 Role)

Terraform은 `~/.aws`의 장기 Access Key로 직접 실행하지 않고, **MFA로 발급한 임시 세션**으로 실행합니다. (03 §3-C.4, §3-F.4.2, §3-F.15)

| 세션 | 실행 주체 | 사용 대상 | 최대 시간 |
|---|---|---|---|
| `personal` | 본인 IAM User + MFA | TF 실행 Role이 없거나 Role 자체를 복구해야 할 때 | 1시간 (`TF_SESSION_SECONDS`로 조정) |
| `bootstrap` | `seokpan-tf-bootstrap` | `terraform/bootstrap` | 1시간 |
| `foundation` | `seokpan-tf-foundation` | `terraform/foundation` | 2시간 |
| `rosa` | `seokpan-tf-rosa` | `terraform/rosa` | 4시간 |

```bash
cd ~/work/seokpan-hybrid-infra
source scripts/tf-session.sh foundation   # MFA 코드 입력 → 실행 주체·만료 시각 출력
cd terraform/foundation && terraform plan -out=<작업명>.tfplan

source scripts/tf-session.sh clear        # 작업 후 세션 해제
```

- 반드시 `source`로 실행합니다. 임시 자격증명은 현재 셸에만 있고 파일로 저장하지 않습니다.
- backend(State)와 provider가 같은 세션을 쓰므로, **출력된 실행 주체가 작업 Root와 맞는지** 확인 후 plan/apply 합니다.
- AssumeRole은 프로젝트 사람 IAM User 4명이 **MFA 인증한 경우에만** 허용됩니다. MFA 미등록자는 먼저 등록합니다.
- apply 전에 만료 시각을 확인합니다. 남은 시간이 부족하면 세션을 새로 발급한 뒤 plan부터 다시 실행합니다.
- 새 세션을 발급하면 **기존 세션을 먼저 해제**합니다. 발급에 실패하면 이전 Role이 아니라 기본 자격증명(IAM User) 상태로 남고, 실패 메시지에 현재 실행 주체가 표시됩니다.
- 만료 시각은 KST와 UTC로 함께 표시됩니다.
- MFA 코드는 한 번만 사용할 수 있습니다. 세션을 연달아 발급할 때는 앱의 숫자가 바뀐 뒤 입력합니다.

### MFA 등록 (최초 1회)

> 2026-10-01 기준 팀원 4명 모두 등록 완료. 장치 교체·재등록 시 사용합니다.

본인 리눅스 계정(`su - 본인계정`)에서 실행합니다. MFA 장치는 **1개만** 등록합니다. 스크립트는 첫 번째 장치를 사용합니다.

```bash
# 0) 기본 자격증명이 본인 IAM User인지 확인
aws sts get-caller-identity --query Arn --output text      # …:user/<본인 IAM User>

# 1) 가상 MFA 장치 생성 → QR 이미지 저장 (출력되는 ARN을 메모)
umask 077
aws iam create-virtual-mfa-device \
  --virtual-mfa-device-name <본인 IAM User> \
  --outfile ~/mfa.png --bootstrap-method QRCodePNG \
  --query VirtualMFADevice.SerialNumber --output text

# 2) VS Code에서 ~/mfa.png를 열고 휴대폰 인증 앱으로 스캔

# 3) 연속된 코드 2개로 활성화 (첫 코드 확인 후, 숫자가 바뀌면 두 번째 코드)
aws iam enable-mfa-device --user-name <본인 IAM User> \
  --serial-number <1)에서 출력된 ARN> \
  --authentication-code1 <코드1> --authentication-code2 <코드2>

# 4) QR 이미지 즉시 삭제 (MFA 비밀키가 들어 있음)
rm -f ~/mfa.png
```

- 1)에서 `EntityAlreadyExists`가 나오면 이전 시도에서 만든 미활성 장치가 남은 것입니다. `aws iam delete-virtual-mfa-device --serial-number <ARN>`으로 지운 뒤 다시 실행합니다.
- QR 이미지와 시드 값은 커밋하거나 공유하지 않습니다.

### 처음 설정 확인

```bash
cd ~/work/seokpan-hybrid-infra
source scripts/tf-session.sh personal     # 실행 주체: …:user/<본인>
source scripts/tf-session.sh bootstrap    # 실행 주체: …:assumed-role/seokpan-tf-bootstrap/<본인>-bootstrap
cd terraform/bootstrap
terraform init                            # 예전 clone이면 아래 "기존 clone 사용자 안내" 먼저
terraform plan                            # No changes
cd ../.. && source scripts/tf-session.sh clear
```

- 확인 결과는 담당 Issue에 **성공/실패와 Role 이름만** 남깁니다. 계정 ID·Access Key·MFA 코드는 기록하지 않습니다.
- 이 확인은 init / plan까지입니다. apply는 지정된 실행 주체만 수행합니다.

### Role 권한 경계

| 구분 | bootstrap | foundation / rosa |
|---|---|---|
| 자기 State(`*.tfstate`) | Get / Put | Get / Put |
| 자기 Lock(`*.tflock`) | Get / Put / Delete | Get / Put / Delete |
| List | 버킷 관리 Role | 자기 접두사(`phase2/<root>/`)만 |
| 다른 Root State | 거부 | 거부 |
| State 삭제·Version 삭제·버킷 삭제 | 거부 | 거부 |
| State 버킷 설정 | 관리 | 거부 |
| AWS 서비스·IAM | `seokpan-tf-*` Role 관리만 | **없음** (Root 구현 PR에서 추가) |

- foundation / rosa에 필요한 권한은 각 Root 구현 PR에서 **필요한 Service / Action / Resource만** `terraform/bootstrap/iam.tf`에 추가합니다. bootstrap apply 후 해당 Root를 실행합니다.
- `iam:PassRole`은 대상 Role ARN과 `iam:PassedToService` 조건으로 제한합니다. 생성하는 IAM Role에는 필요하면 Permissions Boundary를 둡니다.
- ROSA Role의 기반 자원 확인은 읽기 전용 조회 권한으로 해결하며, foundation State 읽기 권한을 주지 않습니다. (03 §3-F.15.2)

## 버전 기준

| 대상 | 버전 | 고정 방법 |
|---|---|---|
| Terraform | `1.16.4` | 각 Root `required_version = "1.16.4"`, controller 서버 공용 설치 |
| AWS provider | `6.67.0` | 각 Root `version = "6.67.0"` + `.terraform.lock.hcl` 커밋 |

- AWS provider는 03 초기 후보(6.66.0) 대신 **6.67.0을 채택**했습니다. bootstrap을 6.67.0으로 apply·검증했기 때문입니다. (#10 변경 기록)
- 새 버전을 자동 채택하지 않습니다. 버전 변경(`terraform init -upgrade`)은 PR로 리뷰합니다.

## backend 설정 템플릿

새 Root를 만들 때 `backend.tf`에 아래를 넣고 `<root명>`만 바꿉니다.

```hcl
terraform {
  backend "s3" {
    bucket               = "seokpan-tfstate-847835841591"
    key                  = "phase2/<root명>/terraform.tfstate"
    workspace_key_prefix = "phase2/<root명>/env"
    region               = "ap-northeast-2"
    encrypt              = true
    use_lockfile         = true
  }
}
```

- `workspace_key_prefix`: backend가 init 때 Workspace 목록을 조회(List)하는 경로입니다. 기본값(`env:/`)은 자기 접두사 밖이라 foundation / rosa Role에서 거부되므로 자기 접두사 안으로 둡니다. (bootstrap은 버킷 관리 Role이라 기본값 유지)

> Region은 서울 `ap-northeast-2`로 확정되었습니다. (03 §3-B.3)

## 기존 clone 사용자 안내 (2026-10-01 구조 변경)

`bootstrap/`이 `terraform/bootstrap/`으로, state key가 `phase2/bootstrap/`으로 바뀌었습니다. 이전에 clone한 경우 한 번 실행합니다.

1. `git checkout main && git pull`
2. 예전 `bootstrap/` 폴더에 남길 파일이 없는지 확인한 뒤 삭제합니다.
   `ls -la bootstrap` → `.terraform/`, `.terraform.lock.hcl` 외에 `*.tfplan`, 로컬 state, 개인 메모 등이 있으면 먼저 옮기고 `rm -rf bootstrap`
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

- apply / destroy는 **지정된 실행 주체만** 수행합니다. bootstrap · foundation: 이유빈, rosa: 정태훈 (04 §2.2). 다른 팀원은 init / plan까지 확인합니다.
- 지정 실행자가 아닌 사람이 실행해야 하면 담당자와 인계한 뒤 진행하고, PR·Issue에 **배정 실행자와 실제 수행자**를 함께 기록합니다. (04 §2.4)
- apply는 `main` 코드로만 실행합니다. (예외: bootstrap 최초 구성)
- State 잠금은 충돌 방지 장치이며, 동시 apply를 허용한다는 의미가 아닙니다.
- apply 전 plan의 `destroy` / `replace` 항목을 반드시 확인합니다.
- `root`로 Terraform을 실행하지 않습니다. 각자 `su - 본인계정` 후 **본인 IAM User의 MFA 세션**(`scripts/tf-session.sh`)으로 실행합니다.
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

State 버킷이 유실되면 bootstrap Root도 init할 수 없습니다. 복구는 `personal` 세션(본인 IAM User + MFA)으로 진행합니다.

1. `terraform/bootstrap/backend.tf`를 임시로 다른 이름으로 변경
2. `terraform init -reconfigure` (로컬 state로 전환)
3. `terraform apply`로 버킷 재생성
4. `backend.tf` 원복 후 `terraform init -migrate-state`로 state 재이전

> 버킷은 `prevent_destroy`와 버저닝으로 보호됩니다. 버킷이 유실되면 다른 state도 함께 유실되므로 버킷 삭제는 팀 합의 없이 진행하지 않습니다.
