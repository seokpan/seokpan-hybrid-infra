# foundation

ROSA 삭제와 분리해 관리하는 Cloud Foundation

- state key: `foundation/terraform.tfstate`
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

CIDR, Sizing, Module 구조는 Network / Data 상세설계 후 작성합니다.
