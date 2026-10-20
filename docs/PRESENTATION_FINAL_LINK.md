# 3개 Worker의 3/3 앱 배치와 최소 발표 시험

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/pull/140).

## 실제 상태와 원인

PR103은 승인·병합됐다. Controller는 조회 권한 단계와 준비 Sync를 통과했으나 Backend Ready=1 상태로 180초 뒤 중단했다. 실제 부하·HPA·추가 SQL은 시작하지 않았다. MachineAutoscaler 조회 목록은 빈 배열이며, EC2 전체 개수나 실제 Worker/AZ 수를 의미하지 않는다.

기존 cloud_launch.py의 Source 생성기는 kustomize.replicas에 Backend/Frontend 1을 설정한다. PR103의 Deployment patch replica2만으로는 이 최종 변환기의 count1을 바꾸지 못했다. 이번 재개는 실제 기존 Source·Backend1 Ready·동일 리소스가 이 원인과 일치할 때만 보완한다. 다른 실패이면 쓰지 않고 Pod 대기 이유를 출력한다.

사용자가 기존 3개 EC2를 활용한 앱 분산을 요청했다. 실제 Controller에는 worker 라벨이 있는 Ready 노드6개(각 AZ2개)가 조회됐다. 이것만으로 앱용 Worker6개라고 단정하지 않는다. infra/master/control-plane 역할과 cordon·Ready 상태·앱이 수용하지 않는 NoSchedule/NoExecute taint·기존 nodeSelector를 분류한다. 앱을 배치할 수 있는 기존 노드 중3개를 AZ 분산과 현재 앱 배치 노드 우선으로 선택한다. 추가 노드를 만들거나 나머지 노드를 변경하지 않는다. 적격 노드가3개 미만이거나 단일 AZ이면 실제 제외 이유와 함께 중단한다.

## 변경 범위

- 기존 승인 준비의 MachineAutoscaler 조회·역할 적용과 DB 관측 코드 채택을 재사용한다. 새 역할·EC2·DB·Schema·모니터링 Sync는 만들지 않는다.
- 현재 Application UID·Source·수동 Sync·리소스가 맞을 때 kustomize.replicas의 BE/FE를 각각3으로 변경하고, 기존 Worker hostname 세 개만 사용하도록 Node affinity와 노드/AZ 균등 배치를 설정한다.
- maxSkew1의 topologySpreadConstraints는 정상3개를 세 노드에 분산하면서 롤링의 일시적인 네 번째 Pod도 허용한다. required Pod anti-affinity로 세 노드를 꽉 막지 않는다. Deployment의 기존 교체 전략과 이미지·Pool·Schema·BE 리소스는 유지한다.
- 이 준비용 교체와 발표에서 측정할 단일 Backend 롤링은 구분한다. 실제 3/3 Ready·Pod/Node/AZ 배치가 통과해야 시험 Plan을 만든다. Frontend 준비도 완료한 뒤 정상/롤링 비교 중에는 Frontend를 변경하지 않는다.
- 기존 Source와 Sync 시도 표식은 보존한다. 별도 보완 시도 표식과 Source 전후를 기록하며 응답 유실 후 재제출하지 않는다. 완료 준비는 캐시를 재사용한다.

## 실행 순서

1. Controller jth: 고정 개정의 finish_presentation_link.py --execute --resume-profile. 기존 실패 원인과 맞으면 3/3 준비, 읽기 전용 공유·시험 입력 연결까지 진행한다. 실제 Ready 대기는 최대180초다. 최종 JSON의 PASS와 actual_app_placement를 확인한다.
2. Controller jth: 성공 receipt의 observer_start_argv를 실행한다. Pod/Node와 DB 전체 연결을 최대900초 기록한다. 실제 표본과 프로세스 시작은 구분한다.
3. Windows PowerShell7: Controller 성공 JSON 전체만 클립보드에 복사하고 run_selected_presentation.ps1 -Execute. 기존 Node·Playwright·측정 코드와 제한 SSH 연결을 재사용한다. 정상 6클라이언트/2방, 준비60초+관측180초가 끝난 뒤 같은 조건의 Backend 롤링1회와 추가 관측30초를 실행한다.
4. 정상 시험이 불완전하면 롤링을 시작하지 않는다. 각 시험 프로세스는 최대540초, 중단 파일 이후45초의 정리 여유를 넘기면 종료한다. 이 종료는 클러스터 롤백이 아니다. trigger 응답이 불명확하면 현재 Source·표식을 대조하며 재시도하지 않는다.
5. Controller jth: rolling_control.py verify로 실제 교체 결과를 기록하고 지정된 타임라인·영수증만 비공개 압축한다. Windows는 결과 폴더와 ZIP·SHA256을 출력한다. 완료 상태와 실제 시험 판정은 요청/업무 분모·불명·WSS·Pod/DB 증거를 대조한 뒤 구분한다.

## 중단과 보존

Windows의 presentation-current-local.json에 기록된 abort_file을 만들면 발생기는 이후 작업을 중단한다. 이미 제출된 Backend 교체는 자동 취소하지 않는다. 필요한 경우 기존 rolling_control.py pause/recover/resume을 현재 Argo operation과 Source를 대조한 뒤 명시 실행한다. 결과가 불명확한 상태에서 trigger를 다시 보내지 않는다.

Controller 관측기는 시작 표식·PID·명령을 보존한다. 중단 시 소유자와 /proc 명령이 모두 일치하는 관측 프로세스만 종료한다. 개인 kubeconfig·보호 자격·private.log는 결과 압축에 넣지 않는다.

HPA와 원본 이관·과거 모니터링 복원은 이번 최소 시험에 포함하지 않는다. DB 전체7/한도68 표본은 계정별 가시성·비앱 기준선이나 HPA 통과가 아니다. 시험 입력30분은 자동 자원 삭제나 비용 보장이 아니다. 시험·영상·증거 보존 뒤 실제 삭제는 사용자 확인 후 별도 진행한다.
