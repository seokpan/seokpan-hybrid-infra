# Cloud 기동 전 보조 계층의 실행 계약

## 수락 범위

PR89/90은 기존 앱 배포·정상/롤링·HPA·원본 제어 구현의 채택 범위다. 이 보조 계층의 게시·CI·리뷰·Controller 설치·실제 Run은 서로 구분한다. 이 문서는 새 ROSA 생성을 승인하지 않는다.

Controller 제공 결과에서 named ACL 설치와 필요한 보호 입력 추출은 PASS이며, 이 두 작업은 반복하지 않는다. 메타데이터 전송 등록은 PENDING이고 세부 reason은 아직 수신하지 않았다. 원본 자료 탐색에서 채택되지 않은 항목은 현재 Foundation/네트워크, 비용/시간, DB 한도·예약·Pool 근거, 원본 제어 계약·조회·Writer 계획이다. 파일을 못 찾은 사실을 원본 자체가 없다는 뜻으로 해석하지 않는다.

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
