# 실제 확보한 결과와 발표 사용 범위

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/blob/078b93184790231fbc3b0e03c9ce0b767ab39dab/execution/TJUNG03_FULL_REPOSITORY_AUDIT_20261021.md).

## 확보한 사실

| 항목 | 실제 결과 | 근거와 한계 |
|---|---|---|
| 앱 배포 | Backend Ready 1/1, Frontend Ready 1/1 | Controller Deployment 조회. 전체 단계 Ready 영수증은 아직 없음 |
| 배치 | Backend와 Frontend가 서로 다른 두 Node에서 Running | 이 사실은 Backend 다중 Pod 공유 상태 성공을 의미하지 않음 |
| 이미지 | 두 배포 모두 고정 sha256 digest 사용 | ACTUAL_CONTROLLER_RESULT_20261020.json에 전체 이미지 기록 |
| 계정 | identity_svc, game_svc 모두 TLS와 목적 계정 조회 성공 | 기존 성공 기록 재사용. 실효 계정별 추가 상한 0이며 DB 전체 한도 68은 계속 적용 |
| 현재 모니터링 | 고정 개정 Synced·Healthy, Backend up=1 | 2026-10-20 18:59:45.035 KST의 한 표본. 연속 가용률 또는 부하 내구성으로 확대하지 않음 |
| 외부 업무 | HTTPS 접근, Guest 로비, 테스트 회원 가입·로그인, 방 생성 | 브라우저 실제 수행과 캡처. 경기 종료·결과 조회는 미실행 |

## 아직 확보하지 않은 수치

- DB 전체·계정별 현재 연결 수와 관측 최댓값, PROCESS 권한에 의한 전체 세션 관측 성공
- 단계별 배포 전환시간: 최초 실행·외부 확인을 결합한 완성된 시계 기록이 아직 없다. 마지막 Sync와 scrape 시각의 차이를 배포시간으로 계산하지 않는다.
- 6클라이언트 정상 부하의 요청·업무 성공률과 지연, 정상 대비 Backend 롤링 영향
- HPA 확장·축소 결과. 현재 Deployment의 resources가 빈 값이므로 CPU requests 기반 HPA 준비 완료로 표시하지 않는다.
- Alertmanager 메일 수신 시험, 신규 결과의 cp-01 독립 보존

## 남은 최소 실행

기존 앱·모니터링·SQL 설정을 반복하지 않는다. DB 관측만 최대 90초의 별도 실행으로 한 표본을 확보한다. 실패하면 정확한 미확인 범위를 남기며 다른 성공 결과를 취소하지 않는다. 장시간 HPA나 정상/롤링 정식 시험을 자동 시작하지 않는다.

사용자는 19시 이후 빠른 마무리와 기록 우선을 지시했다. 기존 19시 기준을 성과를 채우기 위한 무기한 실행으로 바꾸지 않는다. 자원 종료·삭제와 비용 잔존 확인은 별도 실제 결과로 기록한다.
