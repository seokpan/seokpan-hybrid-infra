# Cloud GitOps 최소 설치

ROSA 설치와 B01/B05 독립 검사는 완료된 상태에서 실행한다. Cloud에 GitOps가 없다는 실제 조회 결과를 이어받아 GitOps Operator만 설치한다.

`scripts/bootstrap-cloud-gitops.py`는 Controller `jth`에서 기존 검증된 oc와 전용 kubeconfig를 사용한다. 기존 `ansible-playbook`이 필요하며 새 도구를 설치하지 않는다. 최초 설치 선언의 소유자는 Infra이고, 생성한 JSON 형식 Ansible playbook과 선언 원문은 Controller 보호 결과 폴더에 보존한다.

1. 이미 수신한 독립 배치 성공 기록과 설치 대상/도구를 대조한다.
2. 공식 `redhat-operators`의 `gitops-1.21` 채널에서 정확한 `1.21.4` 제공 여부를 확인한다. 없으면 변경 전 중단하며 최신 버전으로 대체하지 않는다.
3. 로컬 Ansible 진입점으로 없는 Namespace·OperatorGroup·Subscription만 생성한다. Subscription은 `startingCSV=openshift-gitops-operator.v1.21.4`, `installPlanApproval=Manual`이다.
4. Subscription이 지정한 InstallPlan UID·단일 CSV·공식 Bundle 출처를 확인하고 그 InstallPlan만 승인한다. 추가 종속 Operator나 다른 CSV는 승인하지 않는다.
5. CSV Succeeded와 기본 Argo CD Available을 기다린다. 실패/대기 초과 시 부분 상태를 보존하고 자동 삭제/재설치하지 않는다.
6. 실제 OAuth Route를 사용해 GitHub OAuth App 등록용 Callback을 보호 파일에 준비한다. Team/OAuth App 존재는 추정하지 않는다.

OLM은 Operator 자체의 CRD·Workload·설치용 RBAC와 기본 Argo 인스턴스를 생성한다. Root/Application/Sync, 사람의 OAuth·RoleBinding, SQL·Pool·Migration·앱 기동, Terraform은 실행하지 않는다. 정상 GitHub 로그인과 비상 경로를 확인하기 전 초기 관리자를 회수하지 않는다. 일반 사용자/Argo SSO·권한 수락과 별개이며 설치 성공으로 B08 전체 완료를 선언하지 않는다.

이미 존재하는 설치 객체는 이 도구 소유 표시와 정확한 spec이 같을 때만 재사용한다. 무관한 GitOps/Argo 설치는 인수하지 않는다. 변경 명령을 자동 반복하지 않으며, 부분 상태 후 재개는 실제 결과를 먼저 확인한다.

검증: `python3 scripts/test-cloud-gitops-bootstrap.py` — 카탈로그 버전·출처, 기존 객체 소유권, InstallPlan UID/CSV/추가 종속성 거부, Ansible 진입점, 모의 전체 성공/중단 경로를 검사한다. 실제 ROSA 설치 결과는 수동 실행 뒤 별도 기록한다.

근거: 기존 설계 `03_DETAILED_DESIGN.md` §3-F.6/14, `04_IMPLEMENTATION_READINESS.md` §5.6. 공식 설치: https://docs.redhat.com/en/documentation/red_hat_openshift_gitops/1.21/html/installing_gitops/installing-openshift-gitops
