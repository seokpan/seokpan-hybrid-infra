# 새 테스트 데이터 우선 Cloud 기동

## 선택한 경로

이번 실행은 1차 회원/게임 데이터를 가져오지 않고 새 테스트 데이터로 Cloud 앱을 기동한다. `data_mode: fresh_test`를 명시한다. VPN·원본 Backend 중지·PR156·덤프 GO·최종 Import는 이 경로의 선행 조건이 아니다. 기존 이관 경로는 `migrated` 또는 기존 입력 형식으로 유지하며 그 경로의 중지·Import 근거를 생략하지 않는다.

ROSA 생성/보류 복원과 이 앱 분기 게시·리뷰는 별도다. 현재 네트워크·비용·가동시간·새 Plan과 사용자 시작 지시가 충족되면 기존 생성 도구로 생성 대기를 진행할 수 있다. 새 앱 분기는 검토·Controller 채택과 실제 입력 연결 후 사용한다. 이 문서는 기존 PR91의 승인을 새 변경에 적용하지 않는다.

## 계정·TLS·Schema 조건

1. 새 ROSA의 실제 API/Namespace/Secret/CA/Pod UID를 기존 절차로 연결한다. 기존 클러스터 Token/UID를 재사용하지 않는다.
2. 기존 RDS 시작과 새 Worker의 RDS/Redis 연결을 준비한다. 계정 identity_svc/game_svc·Grant·db_admin의 실제 보호 자격·CA·목표 Host를 연결한다. 계정과 Schema 준비는 Import 이후로 미루지 않는다.
3. 대상은 기존 계약의 stone_game과 예상 Schema 20260902_0002다. Schema가 없다면 기존 승인된 초기 생성 절차가 먼저 필요하다. 이번 도구는 DDL·upgrade·stamp·Import·삭제를 실행하지 않는다. Migration current는 초기 생성 명령이 아니다.
4. 새 템플릿 `tools/cloud-readiness/fresh-test-data-handoff.TEMPLATE.NOT_ACCEPTED.json`을 실제 대상·격리·계정·Schema 근거로 연결한다. 템플릿의 false/null은 실행 입력이 아니다. B가 기존 실제 결과에서 작성하고 비밀값은 보호 파일에만 둔다.
5. fresh 분기에서 Import/원본 중지 true를 만들지 않는다. 해당 주장을 true로 넣으면 거부한다. DB/Redis 목표가 실제 배포 입력과 다르면 중단한다.
6. 목적 계정으로 실제 TLS·매칭 사용자·표 접근·계정별 연결 한도를 읽는다. db_admin의 Schema 확인과 7개 업무 테이블의 초기 비어 있음, Redis room/진행 게임/결과 정합성을 읽기만 확인한다. 데이터가 있거나 Schema가 다르면 삭제/변환하지 않고 중단한다.

## Pool과 연결 예산

기존 초기 배포값 Pool2/overflow0/timeout5초·프로세스1·Engine2를 유지한다. 기존 초기 상한7 Pod에서 앱 상한28+예약10=38이다. fresh 분기는 실제 조회한 전체 한도가 이 값과 90% 여유를 수용하고 두 계정이 각각14연결을 수용하는지 확인한다. 과거 max_connections68을 현재값으로 복사하지 않는다. TLS 실패·계정별 한도 미달·서로 다른 전체 한도 결과는 거부한다.

이 초기 선언 계산은 실제 열린 연결수·HPA 용량 수락을 대신하지 않는다. HPA는 정상/롤링과 별도 구간에서 현재 DB 전체/계정·예약·새 노드·surge/종료 중 연결을 결합하며 시나리오 상한6보다 높이지 않는 정책을 최종 config에 반영한다. 가동시간과 비용을 실제 시작 시 수락한다.

## 사용자 실행 기점

| 기점 | 위치 | 직접 할 일·보낼 결과 |
|---|---|---|
| 생성 전 | Controller jth | 현재 Foundation·비용/가동시간 결합 결과 확인. 기존 rosa-recreate-resume.sh로 새 Plan/MFA/생성 확인 입력 후 생성 시작. 생성 대기는 부재 시간에 진행 가능 |
| 생성 완료 | Controller jth·새 OAuth 화면 | 새 연결·보류 복원 결과와 필요한 callback/개인 로그인 조치. 실제 platform handoff/receipt 경로만 전달 |
| DB 준비 | 기존 승인된 Data 실행 위치 | RDS·목적 계정/Grant/TLS·Schema 초기 준비의 실제 결과 연결. VPN을 사용한 1차 덤프/Import는 호출하지 않음 |
| 앱 분기 채택 | Controller jth | 검토된 새 커밋을 기존 app-measurement checkout에 증분 반영. 설정·기존 work를 덮어쓰지 않음. 새 fresh handoff를 B_READY_CONFIG의 기존 final_data_handoff 경로에 명시 연결 |
| 앱 단계 | Controller jth·새 Argo·발생기 | 기존 stages.sh의 stage single → observe 시작 → 같은 Source 명시 Sync → 외부 HTTPS/WSS → accept-external → after-sync. 정상 업무 이후 선정 shared/target |
| 측정 | Controller·발생기/D | 정상/롤링·DB 관측을 같은 구간에 연결. 별도 HPA는 실제 용량·최소60분 구간 수락 후. 최초 단계 시각과 재개·누락을 보존 |
| 종료 | Controller/cp-01·사용자 | 실제 결과 독립 보존 후 새 삭제 확인. NAT/EIP·공통 기반·Data·Snapshot 범위는 별도 유지 |

`prestart.py --config ... --work ... --execute`는 이제 fresh handoff도 처리하지만 기본값만으로 실행하지 않는다. 현재 handoff 경로 이름에 final_data가 남아 있어도 내용의 data_mode로 엄격히 구분한다. 임의 파일을 전체 템플릿으로 덮어쓰지 않는다.

## 실패·재개

- 새로운 분기를 이미 시작한 이전 이관 Run에 끼워 넣지 않는다. 실제 생성 후 아직 앱 Run을 시작하지 않은 상태에서 경로를 선택한다. 기존 입력 fingerprint와 실패 표식은 보존한다.
- 성공한 목적 SQL·Schema/초기 비어 있음·보호 공급·Pod 생성은 같은 binding/work에서 재실행하지 않는다. 이후 정상/공유 단계는 수락한 최초 baseline receipt를 재사용한다.
- 읽기 실패는 기존 --resume-read-only·같은 work로 남은 단계만 재개한다. 비어 있지 않은 데이터는 지우거나 성공으로 바꾸지 않는다.
- 새 분기의 SQL 성공 상태는 PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION다. 실제 Schema/빈 상태 관측 receipt도 함께 있어야 Source 단계로 진입한다. 이 상태는 앱 업무 성공이 아니다.

## 이번 검증 범위

새 분기의 합성 테스트만 로컬 실행했다. 실제 DB/SSH/TLS 네트워크/ROSA/Pull/Sync·기존 완료 설치·런타임 검사는 실행하지 않았다. 새 코드의 CI·리뷰·Controller 채택과 실제 앱 성공은 각각 별도 기록한다.
