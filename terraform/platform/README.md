# platform

- 범위(초안): Namespace, Quota, RBAC, 이미지 pull secret, OpenShift GitOps Operator, Argo CD root Application
- provider: kubernetes / helm (rosa 스택 output 참조)
- state key: `platform/terraform.tfstate`
- rosa 스택 생성 후 실행, rosa destroy 전에 먼저 destroy
- 상태: 기획서 확정 후 작성
