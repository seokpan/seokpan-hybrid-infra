# rosa

반복 생성·삭제하는 ROSA Platform

- state key: `rosa/terraform.tfstate`
- 모델: **ROSA Classic Multi-AZ**
  - 선택 이유: Managed Service가 허용하는 범위에서 Control Plane 진단 가시성을 확보하고 1차 Self-managed Kubernetes와 운영 책임을 비교하기 위함
- 범위: Cluster, Machine Pool, Cluster-specific Operator Role, OIDC
- provider: aws, rhcs (`RHCS_TOKEN` 환경변수로만 인증)
- Lifecycle: **Ephemeral** — Integration / Validation Window마다 생성, Evidence 확보 후 삭제
- 일상적인 비용 절감 destroy 대상은 이 state로 한정

## 참고
- Multi-AZ Classic 최소 구성: Control Plane 3 / Infra 3 / Worker 3
- 생성 시 EC2 vCPU 쿼터 100 이상 필요 (현재 계정 한도 100, 다른 EC2 사용량과 함께 확인 필요)
- Namespace 등 클러스터 내부 리소스는 이 state에서 만들지 않습니다. (GitOps 소유)

Instance Type, Worker 수, API Public/Private 여부는 상세설계 후 작성합니다.
