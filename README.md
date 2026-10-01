# seokpan-hybrid-infra

석판(Seokpan) 2차 프로젝트 — 온프레미스 + AWS(ROSA) 하이브리드 인프라 IaC 저장소

- AWS / ROSA 인프라: Terraform
- 온프레미스 측 설정: Ansible
- 클러스터 위 앱 배포: [seokpan-hybrid-gitops](https://github.com/seokpan/seokpan-hybrid-gitops) (Argo CD)

## 디렉터리 구조

```
seokpan-hybrid-infra/
├── bootstrap/          # Terraform 원격 state용 S3 버킷
├── terraform/
│   ├── network/        # VPC, 서브넷, NAT, VPN
│   ├── rosa/           # ROSA HCP 클러스터, IAM Role, Machine Pool
│   ├── data/           # DB, 백업용 스토리지
│   └── platform/       # 클러스터 초기 설정, GitOps Operator, Argo CD 연결
└── ansible/            # 온프레미스 측 설정 (VPN 게이트웨이 등)
```

> 각 스택의 상세 범위는 2차 기획서 확정 후 갱신합니다.

## 스택 실행 순서와 state

| 순서 | 스택 | state key | 비고 |
|---|---|---|---|
| 0 | `bootstrap` | `bootstrap/terraform.tfstate` | 최초 1회, 완료됨 |
| 1 | `terraform/network` | `network/terraform.tfstate` | 상시 유지 |
| 2 | `terraform/rosa` | `rosa/terraform.tfstate` | 비용 큼, 필요 시에만 생성 |
| 3 | `terraform/data` | `data/terraform.tfstate` | 상시 유지 |
| 4 | `terraform/platform` | `platform/terraform.tfstate` | rosa에 의존 |

- 스택마다 state를 분리해 변경 영향 범위를 나누고, 비용이 큰 스택만 따로 생성·삭제합니다.
- 다른 스택의 값이 필요하면 `terraform_remote_state`로 참조합니다.
- `rosa`와 `platform`을 분리한 이유: kubernetes provider는 클러스터가 있어야 초기화되므로, 클러스터 생성 코드와 같은 스택에 두면 첫 plan이 실패합니다.

## backend 설정 템플릿

새 스택을 만들 때 `backend.tf`에 아래를 넣고 `key`만 바꿉니다.

```hcl
terraform {
  backend "s3" {
    bucket       = "seokpan-tfstate-847835841591"
    key          = "<스택명>/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
```

## 작업 흐름

1. GitHub 웹에서 Issue 등록
2. `main` 최신화 후 브랜치 생성: `feature/<이슈번호>-<작업명>`
3. 코드 작성 → `terraform fmt` / `validate` / `plan`
4. PR 생성 (plan 결과 첨부) → 팀원 1명 승인 → **Squash and merge**
5. `git checkout main && git pull` 후 **다시 plan** → 이상 없으면 apply

### 규칙
- apply는 **main 브랜치 코드로만** 실행합니다. (예외: bootstrap)
- apply 전 plan의 `destroy` / `replace` 항목을 반드시 확인합니다.
- 동시에 같은 스택을 apply하지 않습니다. (`use_lockfile`로 잠금되지만 사전 공유 권장)
- `root`로 Terraform을 실행하지 않습니다. 각자 `su - 본인계정` 후 본인 IAM으로 실행합니다.
- `.terraform.lock.hcl`은 커밋하고, provider 업그레이드(`init -upgrade`)는 PR로 리뷰받습니다.
- `*.tfstate`, `*.tfvars`, `.terraform/`은 커밋하지 않습니다.

## 비용 관리

- AWS 예산 500달러 미만 유지가 목표입니다.
- `rosa`, `platform` 스택은 작업하지 않는 시간(야간, 주말)에 destroy하고 필요할 때 다시 apply합니다.
- `network`, `data`는 비용이 낮아 상시 유지합니다. (기획서 확정 후 조정)
- 모든 리소스에 `default_tags`(Project, Phase, ManagedBy, Component)를 지정해 비용을 추적합니다.

## bootstrap 복구 절차

state 버킷이 유실되면 bootstrap 스택도 init할 수 없습니다. 이 경우 아래 순서로 복구합니다.

1. `bootstrap/backend.tf`를 임시로 다른 이름으로 변경
2. `terraform init -reconfigure` (로컬 state로 전환)
3. `terraform apply`로 버킷 재생성
4. `backend.tf` 원복 후 `terraform init -migrate-state`로 state 재이전

> 버킷은 `prevent_destroy`와 버저닝으로 보호되어 있습니다. 다른 스택의 state는 버킷과 함께 유실되므로 버킷 삭제는 팀 합의 없이 진행하지 않습니다.