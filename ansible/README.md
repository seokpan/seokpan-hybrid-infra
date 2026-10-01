# ansible

On-Prem 측 설정과 GitOps Bootstrap

## On-Prem (범위 초안)
- Hybrid 연결의 On-Prem 측 Gateway (최종 방식은 Network 상세설계에서 확정)
- MariaDB Restore Target
- Recovery Validation용 Redis Runtime
- NFS / Recovery Backup Artifact 동기화
- Jenkins (Retain), Harbor (Recovery Registry) 관련 설정

## GitOps Bootstrap
- ROSA Ready 이후 OpenShift GitOps Operator 설치 → Root Application / ApplicationSet 등록
- 최초 1회 최소 단계만 수행하며, 이후 Application Manifest는 GitOps가 관리합니다.

1차 `seokpan-infra` Ansible 재사용 범위는 Migration Matrix 확정 후 결정합니다.
