# 기존 신규 클러스터를 사용하는 최소 앱 준비

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/pull/140).

관리 접속과 Worker→Data 연결이 완료됐지만 모니터링 이력 볼륨 복원이 기존 PVC 때문에 중단된 경우의 후속 진입점이다. 전체 동일 복원 성공을 주장하지 않고, 기존 검토된 복원 단계 중 앱·현재 관측에 필요한 부분만 재사용한다.

```bash
python3 -I scripts/rosa-minimal-app-bootstrap.py \
  --config /private/current-lifecycle.json \
  --source-scripts /private/reviewed-repository/scripts \
  --out /private/new-minimal-bootstrap-result
```

- 신규 클러스터 ID와 동일 recipe에 묶인 management/fresh-worker-binding 완료 영수증이 필요하다. 해당 단계는 재실행하지 않는다.
- 기존 Snapshot·PV/PVC·모니터링 이력 복원·전체 과거 객체 대조는 호출하지 않는다. 중단 표식도 삭제하지 않는다.
- GitOps Operator·선정된 관리 계정/IDP·Namespace·앱 보호 입력·보류 Application을 기존 함수와 단계 영수증으로 준비한다. 실패한 동일 단계는 자동 재시도하지 않는다.
- 현재 cluster-monitoring-config의 다른 값과 저장소 설정을 유지하면서 enableUserWorkload만 활성화한다. 모호한 설정은 덮어쓰지 않는다.
- 현재 PVC의 Bound/claim UID를 관측한다. 데이터 무결성·전체 모니터링 건강·실제 scrape 성공을 이 결과로 대신하지 않는다.
- callback 수락이 아직 없으면 PENDING_NEW_PERSONAL_LOGIN_CALLBACK과 실제 callback 목록을 반환한다. B의 새 개인 로그인, 실제 목적 SQL/TLS/Schema, 승인 Release의 앱 배포·명시 Sync 및 관측은 후속이다.

구현 검증은 합성 경계에 한정한다. 실제 클러스터·DB·앱·메일·부하 시험 성공과 구분한다.
