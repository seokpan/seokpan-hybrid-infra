# ROSA 모니터링 프로젝트 등록

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/pull/140).

`scripts/register-rosa-monitoring.py`는 고정 GitOps 개정의 모니터링 AppProject·Application을 등록하고 Namespace 범위 Controller 권한과 Argo 조회·Sync 정책을 연결한다.

## 입력

```bash
python3 scripts/register-rosa-monitoring.py INPUT_ROOT \
  --native-run-log NATIVE_RESULT_LOG \
  --cloud-api CLOUD_API_URL --apply
```

`NATIVE_RESULT_LOG`는 `prepare-native-runtime-pull.py`의 표준출력 기록이다. Native PASS 또는 `personal_user=tjung03`이 기록된 STOP에서 보호 kubeconfig 후보를 선택한다. STOP의 사용자 기록만으로 현재 접속을 수락하지 않으며, API·실제 `oc whoami`를 다시 대조한 뒤에만 등록 대상 조회를 시작한다. Native의 인증 이후 단계가 실패해도 모니터링 조회는 독립적으로 진행할 수 있다. 사용자 확인 기록이 없는 STOP은 접속 후보로 수락하지 않는다. `oc`와 최초 등록에 필요한 `ansible-playbook`은 설치돼 있어야 한다.

## 선언과 개정

등록 원본은 GitOps 저장소의 `platform/rosa-monitoring/project/`다. 개정 `16fd14cd1c085a8314a5e2e18b89e2cd3b051626`의 파일 확인값을 대조하고, Application 등록본에 40자리 `targetRevision`을 주입한다. 원본 파일의 자리표시는 수정하지 않는다.

AppProject는 `seokpan-cloud`의 ServiceMonitor·PrometheusRule·NetworkPolicy만 허용한다. Application은 `platform/rosa-monitoring/app`을 참조하며 자동 Sync가 없다. `FailOnSharedResource=true`, `Prune=false`, `Delete=false`를 유지한다.

## 등록 범위

- `seokpan-cloud`: 모니터링 Controller Role·RoleBinding. 대상은 `openshift-gitops/openshift-gitops-argocd-application-controller` ServiceAccount다. 세 리소스의 get/list/watch/create/update/patch만 추가한다.
- `openshift-gitops`: 모니터링 AppProject·Application. 최소 Ansible bootstrap으로 없는 객체만 생성한다.
- Argo RBAC: 지정된 운영자 Group에 해당 프로젝트 get과 Application get/sync를 연결한다. 다른 프로젝트·기본 정책은 보존한다.

생성 전 서버 dry-run과 소유 검사를 수행한다. Argo 정책은 resourceVersion·기존 정책 비교 조건을 포함한 JSON Patch로 변경한다. 같은 이름의 외부 소유 객체나 선언 차이가 있으면 덮어쓰지 않는다.

참조할 Role·AppProject가 없는 경우 RoleBinding·Application의 서버 dry-run은 선행 객체 생성 직후에 수행한다. 검증이 성공해야 해당 객체를 생성한다. 이렇게 지연한 검사는 결과에 별도로 기록하며 선행 객체의 부분 생성도 보호 receipt와 Ansible 로그에 남긴다.

등록 후 Controller 실효 권한 18개와 Argo 정책 반영을 확인한다. 권한 검사는 Namespace 범위 `LocalSubjectAccessReview`를 사용하고, Controller의 사용자 이름과 ServiceAccount 기본 Group을 함께 지정한다. API는 실행 계정에 조회 권한이 있는 `authorization.k8s.io` 또는 `authorization.openshift.io` 중에서 선택한다. 계정 대행(`--as`), Controller Token 발급이나 조회 목적의 추가 권한 부여는 하지 않는다. 조회 권한이 없거나 평가 오류·거부가 있으면 중단한다.

`PASS_MONITORING_REGISTERED_MANUAL_SYNC_PENDING`은 등록·권한 설정 결과이며 운영자 브라우저 권한 수락과 수동 Sync 결과는 별도다. 권한 API의 응답은 실제 Sync·target UP·알림 전달 성공을 증명하지 않는다.

## 등록 후 조회 재개

```bash
python3 scripts/register-rosa-monitoring.py INPUT_ROOT \
  --native-run-log NATIVE_RESULT_LOG \
  --resume-from STOPPED_MONITORING_RESULT_DIRECTORY \
  --cloud-api CLOUD_API_URL
```

`--resume-from`은 생성·Argo 정책 변경을 시도한 뒤 `MONITORING_REGISTRATION_READBACK`에서 중단된 receipt만 수락한다. 보호 결과의 네 객체 선언을 고정 개정·권한 범위와 대조한 뒤 실제 객체, Argo 정책과 남은 권한 결과를 읽는다. Native 단계, Ansible bootstrap, 서버 dry-run, 객체 생성·Patch는 실행하지 않는다. `--apply`를 함께 지정하면 중단한다. 기존 실패 원문은 보호 경로에 유지하고 분류만 출력한다.

`MONITORING_CONTROLLER_AUTHORIZATION`에서 중단된 receipt는 위 재개 대상이 아니다. 권한 조회가 가능해진 뒤 최초 등록 명령에서 `--resume-from` 없이 `--apply`로 다시 실행한다. 기존 객체는 소유·선언이 일치할 때 재사용하며, 모두 존재하고 Argo 정책도 같으면 객체 생성·정책 Patch 없이 권한 조회로 진행한다. 일반 실행은 Ansible 실행 파일 존재·고정 원본 확인과 렌더의 서버 dry-run을 다시 수행한다. 기존 객체나 정책이 달라졌다면 읽기 전용 재개로 간주하지 않으며, 소유·선언 오류는 중단하고 누락 객체 생성·누락 정책 반영 가능성은 최초 등록과 같다.

## 실행 경계와 실패

도구는 Application Sync·smoke·앱·Migration·SQL·Terraform·IAM 변경을 실행하지 않는다. 모니터링 선언은 서버 dry-run만 수행하며, 실제 생성은 수동 Sync로 진행한다.

실패 시 단계, 생성·정책 변경 시도 여부와 보호 결과 경로를 기록한다. 자동 롤백·삭제는 하지 않는다. 부분 생성 객체는 소유·선언을 대조한 뒤 재사용할 수 있다. API 원문·접속 자격은 공개 출력에 포함하지 않는다.

## 검사

```bash
python3 scripts/test-rosa-monitoring-registration.py
```

고정 개정 주입, 자동 Sync·다른 배포 경로 거부, 프로젝트·Controller 권한 범위, 정책 보존, 외부 소유·드리프트 중단과 보호 접속 선택을 오프라인에서 검증한다. Namespace 권한 조회의 두 API 형식·ServiceAccount Group·거부/평가 오류와 설정 변경 없는 조회 재개도 검사한다.
