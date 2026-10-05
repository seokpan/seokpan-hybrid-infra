# ROSA Source 리뷰와 실제 실행의 출발점

이 문서는 [h-infra PR #28](https://github.com/seokpan/seokpan-hybrid-infra/pull/28)의 Source 리뷰와 [h-infra Issue #25](https://github.com/seokpan/seokpan-hybrid-infra/issues/25)의 실제 실행을 연결한다. 승인 설계·Owner·비용 기준을 바꾸지 않는다. 최신 코드와 정적 검사만 준비됐어도 사람의 Source 리뷰를 시작할 수 있다. 실제 foundation 출력이나 전체 A 업무의 종료를 Source 리뷰 요청 조건으로 묶지 않는다.

## 지금 리뷰할 범위

| 리뷰 대상 | 확인할 내용 | 관련 작업 |
|---|---|---|
| B의 rosa Root·State | Cluster 종속 IAM/OIDC·Cluster·Worker→Data Binding, 정본 Key, 승인 주소/초기 Worker, 별도 관리·Secret 경계 | [h-infra Issue #25](https://github.com/seokpan/seokpan-hybrid-infra/issues/25) |
| A의 기반 경계·소비 Schema | VPC/6 Subnet/AZ·Classic 공통 Role/Policy·제한 출력 개정, foundation과 rosa의 중복 Owner 없음 | [h-infra Issue #23](https://github.com/seokpan/seokpan-hybrid-infra/issues/23) |
| C의 Data 의미 | 서로 다른 MariaDB/Redis SG 2개, 기반 Rule과 종속 Binding 소유권·Port·재생성/보존 범위 | [h-infra Issue #19](https://github.com/seokpan/seokpan-hybrid-infra/issues/19) |
| D/A의 Registry·권한 경계 | Worker Pull은 foundation 후속, CI Push 권한과 Runtime Pull 분리, 목적 TF Role의 Backend 권한과 서비스 권한 구분 | [h-infra Issue #18](https://github.com/seokpan/seokpan-hybrid-infra/issues/18), [h-infra Issue #20](https://github.com/seokpan/seokpan-hybrid-infra/issues/20) |
| 생성·삭제·교체 절차 | Binding 해제 → Cluster 제거·실제 서비스 삭제 확인 → IAM/OIDC cleanup. 부분 Replace·삭제 timeout·잔존 대응 | [README](README.md#worker-sg-binding과-삭제-단계) |

리뷰는 해당 PR의 **최신 전체 HEAD**와 diff·동일 HEAD의 실제 Source 검사 결과를 대상으로 한다. 이전 HEAD의 성공을 새 HEAD에 복사하지 않는다. 무료 정적 Source 검사는 실제 Provider 설치·fmt·validate·Schema와 Source/Lock 불변까지이며, 실제 Backend·Caller·IAM 지원·Cloud Plan/Apply를 입증하지 않는다. 과거 로컬 Unix socket BLOCKED는 그 환경의 이력으로 보존한다. 이미 성공한 이전 검사만 확인하려고 같은 HEAD를 불필요하게 다시 실행하지 않는다.

리뷰 코멘트에는 `검토 HEAD / 검토 범위 / 수락 또는 보완 / 남은 실행 Gate`를 적는다. Source의 보완 요구가 해소되고 최신 검사·사람 리뷰를 확인한 뒤 Source 병합을 판단한다. Source 병합으로 Issue #25의 실제 Plan·Runtime·정리 범위를 자동 종료하지 않는다.

## 실제 첫 Plan에서 기다리는 입력

아래 항목은 **실제 수신·확인이 아직 필요한 조건**이다. 문서의 필드와 `INPUT_REQUIRED` 예시는 수신 완료 증거가 아니다. 공개 기록에는 값 대신 받은 개정·출처 전체 SHA·확인 시각·논리 참조·수락 범위·보완 담당을 연결한다.

| 필요한 입력·확인 | 공급·확인 경로 | 이 단계에 요구하지 않는 항목 |
|---|---|---|
| 실제 Account/서울 Region/VPC, Public 3 + ROSA Private 3의 같은 VPC·3 AZ 조합, 최신 기반 Source/출력 개정·생성 조합 | A의 Issue #23 → B의 Issue #25 수신 | Data Private Subnet을 설치 목록에 추가, VPN·복구 Host 전체 완료 |
| Classic Installer/Support/ControlPlane/Worker Role 4개·Prefix/Path, Operator 공통 Policy Map, 적용 Boundary/External ID와 실제 권한·Trust·공유 영향 | A 기반·bootstrap Owner, Issue #20 권한 연결 → B 수신 | Backend 접근만으로 서비스 권한 PASS, 개인 Admin 성공을 목적 Role 성공으로 전용 |
| 서로 다른 실제 MariaDB/Redis Data SG 2개·Owner·기반 Rule 의미 | C의 Issue #19와 A의 Issue #23 → B 수신 | DB/Redis Endpoint·비밀번호·CA·Schema·데이터 이전·전체 백업 |
| B 실행 Controller의 Core/Provider/Lock·CLI 조합, 현재 목적 Caller/세션, 정본 Backend Key/Lock·Account, 실제 서비스 지원/GA patch·Quota·구독·Worker disk | B의 Issue #25, A의 Network/IAM 리뷰 | ROSA 생성·지원·권한이 이미 PASS라는 가정 |
| 실제 입력 수신, 지원·예비 총비용·실행 창의 첫 Plan 준비 리뷰 참조, 보호된 전체 Plan 저장/리뷰 경로 | B 준비, A 기반 리뷰, D 총비용·창 연결 | 아직 없는 실제 Plan을 리뷰·승인했다고 표시 |

첫 Plan은 `cluster_enabled=true`, **`worker_sg_binding=null`**로 준비할 수 있다. 현재 Schema는 이때도 **Data SG 2개가 필수**다. Binding Rule은 0개이며, 실제 Worker SG는 생성 뒤 확인한다. DB/Redis 연결값·App Image Digest는 rosa 첫 Plan 입력이 아니다. Worker Pull 구현과 Data/Secret/Schema 준비는 후속 App 배포 조건으로 관리한다.

값 누락·기반 변경·Caller/Backend 불일치·지원/권한 부족이 있으면 **그 실제 Plan을 대기**하고 계약·코드·Case·OCP/Cloud GitOps 준비는 계속한다. `execution_review`의 문자열 검사는 참조의 형식 확인이며 실제 리뷰·지원·비용 승인 자체를 발급하지 않는다.

## 첫 Plan 이후의 순서

| 단계 | 다음 단계로 넘길 증거 | 계속 보류할 실행 |
|---|---|---|
| 실제 전체 Plan | 동일 Code/Lock/입력·Caller, 실제 생성/교체/삭제·권한·Network/Data 보존 영향의 A 리뷰, 보완 요구 해소 | Plan 없이 유료 생성 |
| 유료 ROSA 생성 | 실제 Plan 수량·단가·현재 누적/잔여/잔존·Window·실패/정리 비용을 포함한 D의 전체 Cost Gate, 실행 범위·창·가동/삭제 책임 확인 및 해당 실행 승인 | Source 병합만으로 Apply/생성 |
| 생성 후 Data 연결 | 실제 현재 Cluster/infra ID·Worker SG·Node/ENI 소속 관측 → 새 Stage 2 전체 Plan → Data SG로 TCP 3306/6379 Rule 2개·Owner/충돌/허용·거부 확인 | 임의 같은 VPC SG를 Worker SG로 수락 |
| App 최초 배포 | 실제 Worker Pull/Image, Data Endpoint/CA·TLS/AUTH·Schema·Cloud Secret, 관리/최소 Bootstrap·GitOps 인계. 필요한 Migration 단일 실행/결과 확인 후 최초 수동 Sync | Cluster Ready를 App 업무 PASS로 표시 |
| Window A/B·복구·재생성 | 정상 Baseline·Backup/Release, 조건부 중간 정리·새 Cluster/Binding/Pull/Context, 실제 분리 장애·부하·전체 T18의 각 Run | 부분 Source/예행을 전체 시험 PASS로 표시 |
| 최종 정리 | 검증 자산·독립 사본/복호화 수단·Evidence 접근 보존, 승인 rosa 삭제의 실제 완료·Orphan/잔존/후속 청구·담당 수락 | foundation/bootstrap 전체 Destroy를 rosa 기본 정리로 실행 |

전체 비용은 **$450 계획선 초과 신규 가동 보류·조정, $500 한도**를 유지한다. Window A 뒤 정리는 백업/자산 접근·쓰기/Binding 보호·범위/비용·실행 승인을 확인한 조건부 작업이다. 계속 가동하면 실제 가동 시간과 누적 비용을 기록한다. ROSA 삭제·잔존 비용 종료·프로젝트 전체 완료는 각각 확인한다.

## 결과를 남기는 위치

실제 Source·입력 수신·Plan·각 실행 결과는 Issue #25와 원 PR/새 Evidence Run에 먼저 기록한다. `NOT RUN`, `BLOCKED`, 실패·부분 결과와 다음 담당을 실제 상태로 남긴다. 보호 Plan/State·실제 입력·Token·비밀번호·Key는 Git/공개 로그에 게시하지 않는다.

본인 진행은 [h-docs Issue #21](https://github.com/seokpan/seokpan-hybrid-docs/issues/21)의 해당 TH와 연결하고, 팀 입력/공유 실행은 [h-docs Issue #8](https://github.com/seokpan/seokpan-hybrid-docs/issues/8)·[WORK_TRACKER](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/WORK_TRACKER.md), 공식 판정은 [05 구현·검증](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/05_IMPLEMENTATION_AND_VALIDATION.md)에 원본 링크를 연결한다. 제출과 수신, Source 리뷰와 실행 리뷰를 별도로 적는다.
