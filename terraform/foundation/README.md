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

## Network 구현 준비

기준: 승인된 03 상세설계 §3-B 및 Infra Issue #23의 통합 합의.

### 현재 Source 구성

- VPC: `192.168.64.0/20`, DNS Support/Hostnames 활성화
- Public Subnet: `192.168.64.0/24` ~ `192.168.66.0/24`
- ROSA Private Subnet: `192.168.67.0/24` ~ `192.168.69.0/24`
- Data Private Subnet: `192.168.70.0/24` ~ `192.168.72.0/24`
- Public 공용 Route Table → IGW
- ROSA AZ별 Route Table → 같은 AZ의 Public NAT Gateway
- Data 공용 Route Table: 인터넷 기본 경로 없음
- Main Route Table은 별도 관리하거나 변경하지 않음
- S3 Gateway Endpoint: Public 및 ROSA Route Table 연결
- 모든 Subnet의 Public IP 자동 할당 비활성화

### AZ 매핑 후보

2026-10-06 Controller 조회에서 아래 AZ가 available이고,
각 AZ에 m5.xlarge 제공이 확인됐습니다.

| 논리 키 | AZ 이름 | AZ ID |
|---|---|---|
| az_a | ap-northeast-2a | apne2-az1 |
| az_b | ap-northeast-2b | apne2-az2 |
| az_c | ap-northeast-2c | apne2-az3 |

EC2 규격 제공 조회는 실제 생성 용량이나 ROSA/RDS/Redis 지원 조합을
보장하지 않습니다. 실제 실행 전에 서비스별 지원 조건을 대조합니다.

### Data와 ROSA 연결

- VPC 참조: `aws_vpc.main.id`
- Data Subnet 참조: `aws_subnet.data`
- Data Subnet ID 목록: `[for subnet in values(aws_subnet.data) : subnet.id]`
- Network 출력: `vpc_id`, `network_subnets`
- ROSA 설치에는 Public 3개와 ROSA Private 3개만 전달합니다.
- `network_subnets`에는 Data ID도 있으므로 전체 출력을 ROSA 입력에 그대로
  전달하지 않습니다. 필요한 필드만 추출하고 소비 계약과 대조합니다.

### 실제 실행 전에 남은 조건

- Data 코드를 같은 foundation Root/State로 통합
- 필요한 Network/Data 서비스 권한의 bootstrap 변경·리뷰·적용
- ROSA Account-wide Role/Policy 및 Worker ECR Pull 후속 준비
- 실제 Data 서비스 지원·AZ 조건과 출력 수신 계약 대조
- S3 Endpoint Policy의 실제 호출 범위 및 ECR Layer 접근 대조
- NAT/EIP 유지·삭제·재생성 조건과 비용 운영 계획 검토
- 목적 MFA Role·Caller 확인, 실제 Backend 초기화
- 전체 foundation Plan·삭제/교체 영향·Cost Gate·팀 리뷰

현재 Network Source와 정적 검사 준비 단계입니다.
실제 AWS 생성·통신·Plan·Apply 완료를 의미하지 않습니다.

### ROSA Classic Subnet 태그

기존 VPC를 사용하는 ROSA Classic의 공식 Subnet 태그 요구사항을 반영합니다.

- Public Subnet 3개: `kubernetes.io/role/elb = "1"`
- ROSA Private Subnet 3개: `kubernetes.io/role/internal-elb = "1"`
- Data Private Subnet 3개: ROSA 설치 대상에서 제외하며 위 태그를 추가하지 않습니다.

각 Subnet 리소스의 `for_each`에 따라 해당 태그를 AZ별 3개에 적용하도록 선언합니다.
현재는 Source 반영 단계이며, 실제 AWS 태그 적용·Preflight 통과는 별도 확인합니다.

공식 기준:
https://docs.redhat.com/en/documentation/red_hat_openshift_service_on_aws_classic_architecture/4/html/prepare_your_environment/rosa-sts-aws-prereqs
