# 이관 준비와 원본 Backend 종료 확인 도구

## 원본 관리 경로 조회

`scripts/collect-cutover-readiness.py`는 Pool·Worker SG 결과 파일과 Replica 0 배포 후보를 입력받아 설정된 Kubernetes Context를 조회한다.

```bash
python3 scripts/collect-cutover-readiness.py INPUT_ROOT COMPLETED_RESULT_DIR HELD_RENDER_DIR --cloud-api CLOUD_API_URL
```

결과 디렉터리 두 개는 `INPUT_ROOT` 바로 아래에 있어야 한다. 배포 후보의 CPU·메모리 설정과 임시 Pull 인증 만료 기록을 읽는다. Secret 값과 kubeconfig 원문은 출력하지 않는다.

조회 경로는 `KUBECONFIG`와 `~/.kube/config`다. 지정한 Cloud API Context를 제외한 Context에서 `application/backend` Deployment와 `argocd/apps-backend` Application을 읽는다. Application의 저장소·경로가 원본 계약과 일치하면 같은 Namespace의 HPA·Job·CronJob을 조회한다. 명령마다 kubeconfig·Context와 30초 제한을 지정한다.

Context·이미지·Replica·자동 동기화·Parent·쓰기 주체 목록은 보호 결과 디렉터리에 저장한다. 공개 기록에는 판정과 검증 범위만 포함한다.

`writer_stop_ready=false`는 조회 결과만으로 쓰기 중지가 승인되지 않았음을 나타낸다. 중지 실행에는 GitOps 제어 구조, 외부 쓰기 주체, 작업 시간과 복귀 기준의 확인이 필요하다. 설정 변경·scale·Sync·SQL·이관·앱 기동은 이 도구의 실행 범위에 포함하지 않는다.

## 경로·배포 자원·임시 Pull 인증

`scripts/resume-cutover-independent.py`는 수집 결과를 입력받아 다음 세 단계를 실행하고 단계별 결과를 보존한다.

- 원본 경로: SSH Host 별칭과 제한된 Ansible inventory에서 후보를 찾는다. `--source-ssh 계정@호스트`와 `--source-api`를 지정한 경우에만 SSH 조회를 실행한다. 인증과 Host Key 검증을 유지하며 새 Host Key 자동 수락이나 키 복사는 허용하지 않는다.
- 배포 자원: 단일 프로세스·Pool 후보에 BE CPU 100m/500m·메모리 128Mi/256Mi, FE CPU 25m/250m·메모리 32Mi/64Mi의 요청/제한을 반영한다. Deployment 두 개를 Replica 0으로 서버 dry-run한다. 이는 실제 부하·스케줄링·Pool 운영값 수락과 구분한다.
- 임시 Pull 인증: Secret 소유와 만료 기록을 확인한다. `--renew-expired-pull`을 지정한 경우에만 고정 해시 ECR 도구로 인증을 갱신한다. 읽기 범위는 프로젝트 FE/BE 저장소이며 자격증명은 메모리와 해당 Secret에 공급한다. IAM 정책·Worker 역할은 변경하지 않는다.

원본 관리 서버에서 조회를 실행하려면 같은 고정 개정의 `REMOTE_READER`와 해당 서버의 kubeconfig·Context를 사용한다. 지정 API와 Source·Parent·쓰기 주체를 조회하며 인증 자료를 다른 서버로 복사하지 않는다.

## Secret 없는 Worker 이미지 실행 확인

`scripts/check-worker-secret-free-pull.py`는 Compute 노드 세 개에서 승인 FE/BE Digest를 각각 `Always`로 Pull하고 `/bin/sh -c 'exit 0'`을 실행한다.

전용 임시 ServiceAccount에는 Token 자동 마운트와 Pull Secret 연결을 사용하지 않는다. 클러스터 공통 Secret 또는 실제 Pod에 연결된 Secret이 해당 ECR 인증을 포함하면 판정을 중단한다. Secret 값은 메모리에서만 읽는다.

노드의 Digest 목록을 기록하되 캐시·Credential Provider와 분리된 Worker 자격증명 추적으로 해석하지 않는다. 신규 노드·12시간 만료 후 시험은 별도 검증 범위다.

임시 Pod 여섯 개와 ServiceAccount는 생성 UID를 기록하고 UID 사전조건으로 삭제한다. 다른 UID의 객체는 보존한다. 생성 결과 불명확·삭제 실패·잔존 객체는 정리 필요로 보고한다. 앱 서버와 SQL은 실행하지 않는다.

## Git 중지 개정의 실행 결과 확인

`scripts/verify-source-backend-stop.py`는 원본 계약 receipt 두 개, 이관 PR 번호와 Backend 선언 SHA-256을 입력받는다. PR의 병합·대상 main·단일 Backend 파일 범위와 병합 파일 해시를 대조한다. 미병합 상태에서는 `HOLD_SOURCE_STOP_PR_NOT_MERGED`를 출력하고 클러스터 조회를 생략한다.

병합 후에는 API·Deployment/Root/Child UID·Source·오버라이드를 대조한다. Backend desired/actual Replica 0, 종료 중 Pod 0, 준비되지 않은 주소를 포함한 EndpointSlice/Endpoints 주소 0, 활성 Job·실행 가능한 CronJob·Backend HPA 부재와 Argo 진행 작업 부재를 확인한다. Child는 병합 개정에 Synced 상태여야 한다.

기본 30초 동안 중지가 유지되면 `PASS_KUBERNETES_BACKEND_STOP_GATE`를 기록한다. Pod 재출현 시 유지 시간을 다시 계산하며 기본 관측 기한은 300초다. 이 도구는 PR 병합·scale·Sync·SQL·Dump를 실행하지 않는다.

최종 Dump에는 외부 쓰기 통제와 DB 복제·최종 쓰기 반영 확인이 추가로 필요하다. Cloud에 새 쓰기가 발생한 경우 원본 재개 전에 데이터 복귀 기준을 적용한다.

## 런타임의 임시 Pull 참조 분리

`scripts/prepare-native-runtime-pull.py`는 FE/BE×Compute 3 실행과 정리 PASS receipt를 입력받는다. Replica 0 Deployment 두 개의 보호 후보와 런타임 ServiceAccount에서 지정된 임시 ECR Secret 참조를 제거한다. 다른 참조·컨테이너·Pool·자원·이미지는 보존한다. Secret 자체는 삭제 범위에 포함하지 않는다.

`--apply`는 resourceVersion과 참조 목록의 비교 조건을 포함한 JSON Patch를 적용한다. GitOps 소유 ServiceAccount는 선언 수정이 필요하므로 중단한다. 결과는 참조 분리와 후보 생성이며 앱 최초 기동·실제 부하·Pool 운영값 수락과 구분한다.

`--resume-from`은 이전 receipt의 중단과 패치 미시도가 확인될 때 허용한다. 실패 단계와 오류 분류를 출력하며 stderr 원문은 보호 디렉터리에 저장한다. 변경 시도 뒤 오류에는 자동 재시도를 수행하지 않는다.

`--reauthenticate-if-required`는 변경 전 조회에서 인증 필요가 확인된 경우 개인 API Token을 숨김 입력받는다. 입력 kubeconfig의 대상·CA를 유지한 별도 보호 kubeconfig를 만들고 코드에 지정된 계정과 로그인 결과를 대조한다. 초기 관리자 자격은 덮어쓰지 않는다. 권한 거부·TLS 오류에는 자동 재인증을 수행하지 않는다. 결과의 `kubeconfig_file`은 해당 실행에 사용된 파일이다.

이전 보호 디렉터리의 개인 kubeconfig가 있으면 먼저 사용한다. 새 Token의 인증 거부는 `PERSONAL_LOGIN_AUTHENTICATION_REQUIRED`, 로그인한 계정 불일치는 `PERSONAL_USER_MISMATCH`로 구분한다.

접속 Token은 해당 클러스터 OAuth의 `/oauth/token/request`에서 발급한다. `Display Token`으로 표시되는 `Your API token` 또는 같은 화면의 단일 `oc login --token=... --server=...` 명령을 숨김 입력한다. Red Hat Cluster Manager의 ROSA CLI Token과 OAuth App Client Secret은 클러스터 API Token으로 사용하지 않는다. 입력 명령은 실행하지 않고 Token·서버 필드만 파싱한다. 다른 서버·추가 옵션·셸 구문은 거부한다.

## 오프라인 검사

다음 검사는 모의 CLI·응답을 사용하며 실제 클러스터·GitHub·DB 인증을 실행하지 않는다.

```bash
python3 scripts/test-cutover-readiness.py
python3 scripts/test-cutover-independent.py
python3 scripts/test-worker-secret-free-pull.py
python3 scripts/test-source-backend-stop.py
python3 scripts/test-native-runtime-pull.py
```

검증 범위는 대상 선택, SSH Host Key/API 고정, 배포 후보·Secret 참조 보존, UID 기반 정리, 병합 개정·종료 상태 판정, 개인 인증 격리와 실패 후 재개 조건이다.
