# 원본 클러스터 쓰기 중지 준비 조회

`scripts/collect-cutover-readiness.py`는 완료된 Pool·Worker SG 결과를 재사용하고, 현재 사용자가 이미 가진 kubeconfig에서 원본 클러스터의 관리 경로를 찾는다. ROSA 관리 접속과 설치 검사를 다시 실행하지 않는다.

```bash
python3 collect-cutover-readiness.py INPUT_ROOT COMPLETED_RESULT_DIR HELD_RENDER_DIR --cloud-api CLOUD_API_URL
```

입력 결과 디렉터리 두 개는 입력 Root 바로 아래에 있어야 한다. 준비한 배포 객체의 CPU·메모리 설정과 기존 임시 Pull 인증 만료 기록을 읽는다. Secret 내용이나 kubeconfig 원문은 출력하지 않는다.

기존 `KUBECONFIG` 경로와 사용자 `~/.kube/config`만 사용한다. 지정한 Cloud API Context는 제외한다. 다른 Context에서는 `application/backend` Deployment와 `argocd/apps-backend` Application을 읽는다. Application Source가 기존 원본 저장소·경로와 일치하는 경우에만 같은 Namespace의 HPA·Job/CronJob을 추가 조회한다. 각 명령에 kubeconfig와 Context를 명시하며, 명령 제한 시간은 30초다.

결과는 새 보호 디렉터리에 저장한다. Context 이름·이미지·Replica·자동 동기화·Parent 참조·쓰기 주체 목록은 운영 자료이므로 전체 결과를 공개 댓글에 붙이지 않는다. 공개 기록에는 대상 일치와 필요한 검증 판정만 사용한다.

이 조회는 원본 쓰기 중지의 실행 승인이 아니다. GitOps Parent/ApplicationSet·외부 CI·관리 프로세스 등 모든 쓰기 주체와 실제 수행자·사용창·복귀 경로의 수락은 별도다. 도구는 `writer_stop_ready=false`를 유지하며, 자동 동기화 변경·scale·Sync·SQL·DB 이관·앱 기동·ECR 갱신을 실행하지 않는다. 보호 자료가 없거나 조회가 실패하면 해당 항목은 미확인으로 남긴다.

오프라인 검증은 `python3 scripts/test-cutover-readiness.py`로 실행한다. 가짜 CLI 응답만 사용하며 실제 인증·클러스터·DB에 접속하지 않는다.

## 독립 후속 세 항목

`scripts/resume-cutover-independent.py`는 기존 수집 결과를 입력으로 원본 경로·배포 자원·임시 Pull 인증을 독립 실행한다. 한 항목이 실패해도 다른 항목의 결과를 보존한다.

- 원본 경로: 현재 사용자의 SSH Host 별칭과 제한된 Ansible inventory 파일에서 후보 이름만 찾는다. 후보에 자동 접속하지 않는다. `--source-ssh 계정@호스트`와 `--source-api`를 명시하면 기존 SSH 인증·검증된 Host Key로 해당 서버에서 읽기 조회를 수행한다. 비밀번호 대화형 입력·새 Host Key 자동 수락·kubeconfig/키 복사는 하지 않는다. 원본 API와 Argo Source가 일치해야 쓰기 주체 목록을 읽는다.
- 배포 자원: 기존 lab의 초기 검증 자원값과 저장된 Pool/단일 프로세스 후보를 보호 배포 파일에 합친다. BE는 CPU 100m/500m·메모리 128Mi/256Mi, FE는 CPU 25m/250m·메모리 32Mi/64Mi의 요청/제한을 사용한다. 변경된 Deployment 두 개만 서버 dry-run하며 replicas0·기존 이미지/Secret 참조를 유지한다. 실제 Cloud 부하·스케줄링 수락이나 운영 Pool 채택이 아니다.
- 임시 Pull: 현재 프로젝트 Secret의 소유·만료 기록을 확인한다. `--renew-expired-pull`이 있을 때만 만료/만료 임박 인증을 기존 고정 해시 ECR 도구로 갱신한다. 프로젝트 FE/BE 두 저장소 읽기 범위를 유지하고 자격증명은 메모리와 해당 Secret에만 공급한다. IAM 정책·Worker 역할은 변경하지 않는다. Node Role 기반 Pull 성공으로 판정하거나 기존 이미지 Pull 시험을 반복하지 않는다.

이 후속도 SQL·이관·원본 쓰기 중지·실제 앱 배포/Sync는 실행하지 않는다. `python3 scripts/test-cutover-independent.py`는 잘못된 SSH 대상, Host Key/API 고정, Secret 참조 보존, replicas0·dry-run, 기존 인증 재사용과 갱신 조건/해시 검사를 오프라인에서 확인한다.

## Worker Pull의 별도 실행 확인

`scripts/check-worker-secret-free-pull.py`는 Worker 정책 적용 확인 뒤 실행하는 별도 시험이다. 완료한 자원 검사·기존 임시 Pull 인증 갱신 결과를 재사용하며 기존 정책·인증을 수정하지 않는다. 기존 Compute 노드 세 개에서 승인된 FE/BE Digest를 각각 `Always`로 Pull하고 `/bin/sh -c 'exit 0'`만 실행한다. 앱 서버·SQL은 시작하지 않는다.

전용 임시 ServiceAccount는 Token 자동 마운트와 Pull Secret 연결을 사용하지 않는다. 클러스터 공통 Secret 및 Pod에 실제로 연결된 Secret에 해당 ECR 인증이 있으면 판정을 중단하며, Secret 내용은 메모리에서만 읽는다. Node 이미지 목록의 기존 Digest 존재 힌트도 결과에 남긴다. 기존 캐시와 Credential Provider 동작 때문에 Pod 실행 성공을 Worker 역할 자격증명 사용의 독립적인 추적으로 확대하지 않는다. 새 노드 교체·12시간 후 만료 시험은 포함하지 않는다.

임시 Pod 여섯 개와 ServiceAccount는 생성 응답의 UID를 기록하고 UID 사전조건으로만 삭제한다. 같은 이름의 다른 UID는 보존한다. 삭제 후 실제 잔존 여부를 읽어 확인하며, 생성 결과 불명확·삭제 실패는 정리 필요로 보고한다. 기존 Pod·Secret·서비스 계정·IAM·DB에는 변경하지 않는다.

원본 SSH가 불가능하지만 기존 원본 관리 터미널에 접속돼 있다면, 같은 고정 개정의 `REMOTE_READER`를 해당 서버에서 로컬로 실행할 수 있다. 기존 kubeconfig/Context와 지정 API만 사용하고 키를 다른 서버로 복사하지 않는다. Source·Parent·쓰기 주체 조회만 수행하며 중지 제어·사용창·복귀 경로 수락은 별도다.

## 이관 중지 개정 반영 뒤의 읽기 확인

`scripts/verify-source-backend-stop.py`는 수락한 원본/관리 구조 receipt 두 개와 이관 PR 번호, 승인된 Backend 선언 SHA-256을 입력받는다. GitHub에서 해당 PR의 실제 병합·대상 main·단일 Backend 파일 범위·병합 개정의 파일 해시를 확인한다. PR이 미병합이면 `HOLD_SOURCE_STOP_PR_NOT_MERGED`를 출력하고 원본 클러스터는 조회하지 않는다.

병합 후에는 기존 kubeconfig를 그대로 사용해 API·Deployment/Root/Child UID·Source main/path·오버라이드 부재를 대조한다. Backend desired/actual Replica0, 종료 중 Pod까지0, 준비되지 않은 주소를 포함한 EndpointSlice/구형 Endpoints 주소0, 활성 Job·실행 가능한 CronJob·Backend HPA 없음, 진행 중 Argo 작업 없음과 Child의 병합 개정 Synced를 확인한다. 기본30초 동안 중지 상태가 유지돼야 `PASS_KUBERNETES_BACKEND_STOP_GATE`를 기록한다. 중간에 Pod가 다시 나타나면 유지 시간을 다시 계산한다. 관측 기한은 기본300초이며 기한 내 확인되지 않으면 중단한다.

이 도구는 조회와 보호 receipt 저장만 수행한다. PR 병합·scale·Sync·재시작·SQL·Dump를 실행하지 않는다. Kubernetes 중지 증거가 외부 CI/수동 쓰기 주체의 통제나 DB 복제/최종 쓰기 반영 확인을 대신하지 않는다. 해당 수락과 이관 담당의 DB 확인이 갖춰져야 최종 덤프로 넘어간다. 복귀는 Cloud 새 쓰기 여부와 데이터 판단 후에 수행한다.

새 오프라인 검증은 `python3 scripts/test-source-backend-stop.py`다. 실제 클러스터·GitHub·DB를 호출하지 않는다.

## 성공한 Pull 증거의 앱 설정 반영

`scripts/prepare-native-runtime-pull.py`는 승인 FE/BE×Compute3 실행 및 정리 PASS receipt를 재사용한다. 기존 자원/Pool 후보의 Replica0 Deployment 두 개와 프로젝트 런타임 ServiceAccount에서 알려진 임시 ECR Secret 참조만 제거한다. 다른 Pull 참조·컨테이너 설정·Pool·자원·승인 이미지는 유지하며 별도의 보호 후보 파일을 만든다. `--apply`는 ServiceAccount의 해당 목록만 resourceVersion과 기존 목록 비교 조건을 포함한 JSON Patch로 반영한다. GitOps 소유 ServiceAccount이면 선언 Owner 검토를 위해 중단한다.

임시 Secret 자체는 다른 작업자가 사용할 수 있어 삭제하지 않는다. Pod·앱·SQL·IAM·기존 Pull/서버 admission 시험을 실행하지 않는다. 결과는 알려진 임시 참조의 분리와 새 후보 준비이며, 실제 앱의 최초 기동/Pull 또는 Worker 자격증명의 독립적인 추적을 완료했다는 뜻은 아니다. 이후 배포에서는 새 후보와 receipt를 사용하고 기존 임시 인증 갱신 도구를 다시 실행해 참조를 되붙이지 않는다.
