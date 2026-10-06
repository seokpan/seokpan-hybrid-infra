# foundation

ROSA 삭제와 분리해 관리하는 Cloud Foundation

- state key: `phase2/foundation/terraform.tfstate`
- 범위
  - VPC / Subnet / Route / NAT / Security Group
  - Amazon RDS for MariaDB Multi-AZ (Primary DB)
  - Amazon ElastiCache for Valkey 7.2 (Cluster Mode Disabled, Primary 1 + Replica 1, Multi-AZ, Auto Failover — 10-06 Redis OSS에서 변경, infra #19)
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

### NAT 비용 Window 안전수칙

`enable_nat_gateways`는 NAT Gateway 비용을 승인된 Window에만 발생시키기 위한
운영 제어 변수이며 기본값은 `false`입니다.

- ROSA가 생성되어 운영·검증 중인 동안에는 `enable_nat_gateways = true`를 유지합니다.
- `true → false` 전환과 그에 따른 Apply는 **ROSA 삭제 완료를 확인한 이후에만** 수행합니다.
- Window 중 Plan/Apply에서는 검토된 실행용 tfvars에
  `enable_nat_gateways = true`를 명시합니다.
- CLI의 `-var='enable_nat_gateways=true'`만 단독으로 사용하지 않습니다.
  실행 시 옵션 누락으로 기본값 `false`가 적용되는 사고를 방지하기 위한 기준입니다.
- 실행용 tfvars는 저장소에 커밋하지 않습니다.
- `false`로 전환하면 NAT EIP, NAT Gateway, ROSA의 `0.0.0.0/0` NAT Route가
  함께 제거됩니다. EIP도 반납되므로 다음 Window 재생성 시 NAT 공인 IP가
  변경될 수 있습니다.

### Data 파일과 입력

Data 코드는 `data_*.tf`(Data SG · RDS · Valkey · Backup S3 · Backup User)와
전용 변수 `data_variables.tf` · 출력 `data_outputs.tf`로 나뉘어 같은 Root/State에 있습니다 (infra #19).

- 공통 입력 `onprem_job_host_cidrs`(`variables.tf`): 온프렘 작업 Host `/32` 목록.
  Data RDS SG 규칙과 #16 Data Route · VPN 경로가 같은 값을 씁니다. 기본값 `[]`이면 규칙 · 경로를 만들지 않습니다.
- `redis_auth_token`: 기본값이 없는 write-only 입력입니다. Plan · Apply 전에 SOPS 원본에서
  현재 셸의 `TF_VAR_redis_auth_token`으로만 공급하고, 실행용 tfvars · 명령 인자에 넣지 않습니다.
  값이 State · Plan에 남지 않으며 `redis_auth_token_version`이 바뀔 때만 새 Token이 전송됩니다.
- Data 출력: Data SG ID 2개(rosa 입력) · RDS / Valkey Endpoint · Port · 마스터 Secret ARN · Backup 버킷 · User 이름.
  비밀값은 출력하지 않습니다.

### 실제 실행 전에 남은 조건

- Data 코드를 같은 foundation Root/State로 통합 (infra #19 PR — 리뷰 · 병합 후 완료)
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
