# Cloud 기동 전 보조 계층의 실행 계약

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/blob/078b93184790231fbc3b0e03c9ce0b767ab39dab/execution/TJUNG03_FULL_REPOSITORY_AUDIT_20261021.md).

## 수락 범위

PR89/90은 기존 앱 배포·정상/롤링·HPA·원본 제어 구현의 채택 범위다. 이 보조 계층의 게시·CI·리뷰·Controller 설치·실제 Run은 서로 구분한다. 이 문서는 새 ROSA 생성을 승인하지 않는다.

제공된 실행 근거에서 named ACL·보호 입력 추출·제한 전송 설치와 새 원격 상태 조회는 PASS로 수락했다. 완료한 작업은 반복하지 않는다. 현재 네트워크 준비·DB 전역 관측 권한·추가 Writer 통제까지 수락한 결과는 아니다. 원본 계약·조회 파일은 원본 호스트의 기존 위치로 연결하며 Controller 로컬 부재를 원본 부재로 해석하지 않는다. 시작 시각과 갱신 비용은 실제 시작 시 반영한다.

## 고정 단계와 판정

| 순서 | 진입·종료 판정 | 실패 또는 재개 |
|---|---|---|
| 1 준비 | 원본 위치·개정·해시가 유일하고 BEFORE_CREATE 미충족 목록이 없음 | 실패 하위 receipt만 확인. 성공한 해독/ACL 반복 금지 |
| 2 생성·보류 구성 복원 | 별도 사용자 시작 지시, 현재 Foundation·예산·실행시간 수락 후 기존 lifecycle 사용 | 기존 State·변경 표식 대조. 새 work로 부분 생성 회피 금지 |
| 3 새 연결·Data | 새 ID/API/OAuth/UID와 실제 최종 Import·목적 SQL·Grant·Schema 결과를 연결 | 원본 중지·덤프 GO는 기존 cutover 절차. Cloud 쓰기 후 원본 단순 재개 금지 |
| 4 앱 단계 | stage → 명시 Sync → observe → 같은 binding/nonce 외부 HTTPS/WSS → after-sync | 같은 stage clock과 work를 재사용. 최초 진입 시각을 다시 만들지 않음 |
| 5 정상/롤링 | 같은 이미지·기본 Replica·부하, FE 유지, Backend 롤링 1회. DB 관측은 동일 구간 | pause/reconcile/recover. 결과 불명 trigger 재시도 금지 |
| 6 선택 HPA | 정상/롤링과 별도 구간, 현재 계정/글로벌·surge·terminating·노드 상한, 같은 Run guard 신선도 | abort → stop/reconcile → 같은 종료 receipt로 finish → verify-restore |
| 7 보존·종료 | Source/Image/Schema·시간선·실패/누락·원문/요약 독립 보존 | 새 삭제는 별도 사용자 확인. NAT/EIP·공통 기반·Data·Snapshot 범위 유지 |

단계별 전환시간은 최초 실제 stage execute부터 같은 Source의 Ready와 외부 접속 확인까지이며 ROSA 구축시간과 섞지 않는다. HTTP 요청·업무 시도 분모, 확정 실패·결과 불명·재시도, WSS 종료/재연결/수렴을 분리한다. DB는 설정 상한과 실제 관측 최댓값을 구분하고 실제 수집 간격과 누락을 남긴다.

원본 계약·조회·Writer 계획의 미충족은 선택한 데이터 이관 전 목록에 남긴다. ROSA 생성 자체의 선행 조건으로 두지 않는다. 단계 분류 변경으로 실제 원본 중지·덤프 GO·전환/복귀 검증을 생략하거나 미확인 입력을 수락하지 않는다. 메일 원본은 선택한 메일 시험 전, 새 노드 용량은 실제 HPA 전 조건이다.

## 실제 입력과 책임

| 입력 | 지금 필요한 기존 자료 | 생성 또는 Data 실행 후 확보 |
|---|---|---|
| 플랫폼 | 현재 Foundation 출력·네트워크 근거·실제 실행 경로 | 새 API/Ingress/Worker SG/OAuth/개인 Context/UID |
| Data | 현재 보호 원본·CA·실행 대상/명령, 한도·예약 근거 | 회전 Gate·최종 Import/계정/Schema/SQL·실제 연결수 |
| 비용 | 현재 비용 근거·유지/변동/미반영분·시작/종료·정리 여유 | 실제 가동 시간과 잔존 비용 |
| 원본 제어 | Context·외부 쓰기 차단·Writer/Argo/HPA 계약과 실행 위치 | 실제 중지 확인·덤프 GO·전환/복귀 판정 |
| 관측 | 채택 코드·발생/수집 경로·메일 선정 시 SMTP 보호 원본 | 실제 target UP/수신·측정 결과 |

형식 변환과 경로 연결은 보조 계층이 수행한다. 외부 담당자의 새 보고서 작성이나 완료한 시험 반복을 요구하는 계약이 아니다. 아직 수신되지 않은 현재 원본 사실을 생성 후 값으로 미루거나 true로 수락하지 않는다.

## 보존 한계

EBS Snapshot은 볼륨별 시점의 보존이며 application-consistent 복원을 자동 입증하지 않는다. 기존 독립 해독 PASS, 권한 DryRun PASS, 보류 복원 recipe와 실제 새 클러스터 복원 성공은 별개다. 전체 RTO/RPO·장시간 부하·별도 장애 시험은 선정 또는 실행 근거가 없으면 미수행으로 유지한다.
