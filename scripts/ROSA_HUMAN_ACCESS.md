# GitHub 개인 로그인 후 ROSA·Cloud Argo 권한 연결

GitHub IdP로 OpenShift에 로그인해도 플랫폼 관리 권한과 Argo 프로젝트 권한은 자동으로 부여되지 않는다. `configure-rosa-human-access.py`는 이미 관측된 B 계정 `tjung03`에 한해 두 권한을 연결한다. [승인 설계 3-C.5/3-C.6](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/design/03_DETAILED_DESIGN.md)과 [관리 인증 추적](https://github.com/seokpan/seokpan-hybrid-docs/issues/21)의 후속이다.

## 범위

- 기존 보호 결과의 성공한 Data 연결 인계에서 Cluster/API를 읽고 현재 접속 대상과 대조한다. 설치·노드·ECR·DB 시험을 반복하지 않는다.
- 실제 OpenShift User와 `seokpan-github` Identity의 이름/UID 연결을 확인한다. 사용자·IdP·OAuth App을 새로 만들지 않는다.
- ROSA 공식 `rosa grant user dedicated-admin`으로 B의 플랫폼 관리 권한을 연결한다. **dedicated-admin은 App Namespace만의 권한이 아닌 넓은 고객 관리자 권한**이다. 일반 팀원 전체에 부여하지 않고 B 플랫폼 담당 계정 하나만 대상으로 한다. cluster-admin은 새로 부여하지 않는다.
- 별도 OpenShift Group `seokpan-cloud-gitops-operators`에 B만 넣고, Argo의 `seokpan-cloud-app` 프로젝트에 `applications get/sync`, `projects get`을 허용한다. 그룹 이름은 GitHub Team 이름과 구분한다.
- 기존 Dex/OpenShift OAuth와 관리자 정책 두 줄·빈 기본 정책을 유지한다. ArgoCD CR의 RBAC만 비교 후 변경하고 Operator가 실제 ConfigMap에 반영한 결과를 확인한다. 소유 ConfigMap을 직접 수정하지 않는다.
- 기존 그룹의 소유권/구성원이 다르거나 기존 권한 규칙과 충돌하면 변경하지 않는다. 다른 GitOps Application이 ArgoCD CR을 이미 소유하면 덮어쓰지 않는다. CR 변경은 resourceVersion과 기존 RBAC에 대한 JSON Patch test를 포함한다.
- AppProject/Application 생성, Sync, 앱 기동, SQL, AWS IAM, 다른 팀원 권한, 초기 관리자/기존 Token 회수는 하지 않는다. 향후 GitOps가 이 CR/Group을 관리할 때 보존한 선언을 인계하고 수동 도구와 중복 소유하지 않는다.

## 실행

Controller `jth`에서 기존 관리 `activate.sh`를 source하고 기존 ROSA CLI 로그인 상태를 준비한다. 성공한 ROSA 작업에 사용한 계정의 세션을 재사용한다. 비밀번호/MFA/Client Secret을 인자로 받지 않는다.

```bash
python3 scripts/configure-rosa-human-access.py /private/existing-rosa-root \
  --rosa-cli /private/existing/rosa --apply
```

`--apply`를 생략하면 실제 사용자·기존 설정·서버 dry-run 확인까지만 한다. 기존 권한/그룹이 정확히 일치하면 재생성하지 않는다. 실패 시 자동 재시도하지 않으며 결과 폴더의 mutation-attempts.json에 실행을 시도한 단계를 남긴다. 일부 단계가 반영됐다면 삭제/롤백을 자동 수행하지 않는다. 수동 재개 시 실제 일치한 상태를 재사용한다.

성공 상태 `PASS_PERSONAL_OPENSHIFT_AND_SCOPED_ARGO_CONFIGURATION`은 그룹 및 Operator 반영 완료다. 실제 사용자 브라우저 세션의 권한, Argo SSO의 Group claim과 허용/거부 결과는 별도로 확인해야 한다. 출력된 Argo URL에서 Log in via OpenShift→GitHub 제공자를 이용한다. 이전 세션에 Group 정보가 없을 수 있으므로 새 세션으로 확인한다. 아직 AppProject/Application을 만들지 않았으면 Argo 목록이 비어 있을 수 있다. 초기 관리자 접속은 정상/비상 관리 경로의 검증이 끝날 때까지 유지한다.

## 검증

```bash
python3 scripts/test-rosa-human-access.py
```

오프라인 18개 통과: 실제 Identity 연결/UID·다른 사용자 거부, 보호 인계 대상 재사용, 기존 Argo 정책 보존/중복 방지/충돌 거부, 넓은 기본 정책·다른 SSO·기존 GitOps 소유권 거부, 그룹 소유권/구성원 경계, 정확한 Cloud 권한, CR 비교 후 변경 조건. Controller 실제 실행과 브라우저 역할 수락은 아직 미실행이다. 오프라인 테스트는 실제 Casbin 판정 또는 IdP 실효 권한 검증을 대신하지 않는다.

근거: [ROSA dedicated-admin 공식 경로](https://docs.redhat.com/en/documentation/red_hat_openshift_service_on_aws_classic_architecture/4/html/install_rosa_classic_clusters/rosa-sts-accessing-cluster), [GitOps Dex/OpenShift Group](https://docs.redhat.com/en/documentation/red_hat_openshift_gitops/1.21/html/access_control_and_user_management/configuring-sso-for-argo-cd-using-dex), [Argo RBAC](https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/).
