# foundation

ROSA 삭제와 분리해 관리하는 Cloud Foundation

- state key: `phase2/foundation/terraform.tfstate`
- 범위
  - VPC / Subnet / Route / NAT / Security Group
  - Amazon RDS for MariaDB Multi-AZ (Primary DB)
  - Amazon ElastiCache for Redis OSS (Cluster Mode Disabled, Primary 1 + Replica 1, Multi-AZ, Auto Failover)
  - Amazon ECR (Cloud Runtime Primary Registry)
  - Backup용 S3
  - AWS 측 Hybrid 리소스
  - ROSA Account-wide Role / Policy

| 리소스 | Runtime Lifecycle |
|---|---|
| VPC / Base Subnet, ECR, Backup S3 | Persistent |
| RDS | Stoppable |
| ElastiCache | 유지 또는 Terraform 재생성 |
| NAT Gateway | Re-creatable 후보 |

## 불변조건
- 정상 사용자 경로(User → ROSA → RDS/Redis)에 Hybrid Tunnel을 포함하지 않음
- RDS, ElastiCache를 Public Internet에 노출하지 않음
- On-Prem, VPC, ROSA Machine/Service/Pod, Tunnel CIDR 충돌을 사전에 제거

Network / Data 구현은 승인된 상세설계와 팀 인계 기준에 따라 진행합니다.

## 공통 실행 설정

버전 기준은 저장소 최상위 README의 「버전 기준」을 따릅니다.

- `versions.tf`: Terraform 1.16.4, AWS Provider 6.67.0
- `backend.tf`: foundation 전용 S3 State 경로와 Lock 설정
- `providers.tf`: AWS Region과 공통 Tag 설정
- `variables.tf`: 공통 입력 변수
- `.terraform.lock.hcl`: Provider 버전과 검증 정보

Backend 설정:

- State key: `phase2/foundation/terraform.tfstate`
- Workspace 조회 경로: `phase2/foundation/env`
- Region: `ap-northeast-2`
- 실제 State 버킷 이름은 Backend 초기화 시 `-backend-config`로 전달합니다.

Data·Registry/CI·Network 파일은 같은 `terraform/foundation` 폴더의
하나의 Root/State에서 관리합니다. 담당 영역별 단독 Apply는 하지 않습니다.

공통 출력은 Network 구현 시 실제 전달할 값에 맞춰 추가합니다.
현재는 빈 `outputs.tf`를 만들지 않습니다.

### AWS 자원을 생성하지 않는 정적 검사

저장소 최상위 폴더에서 실행합니다.

```bash
terraform -chdir=terraform/foundation fmt -check
terraform -chdir=terraform/foundation init -backend=false
terraform -chdir=terraform/foundation validate
```

`init -backend=false`는 S3 Backend 연결을 생략합니다.

`validate` 성공은 코드의 구성 검사가 통과했다는 의미이며,
AWS 권한·실제 통신·전체 Plan 검증 완료를 의미하지 않습니다.

실제 Backend 초기화와 전체 Plan은 MFA foundation 세션,
State 버킷 입력, 필요한 코드와 입력값이 준비된 뒤 진행합니다.

Apply는 전체 Plan·리뷰·비용 검토 후 실행 담당자가 수행합니다.
