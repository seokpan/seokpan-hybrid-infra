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
