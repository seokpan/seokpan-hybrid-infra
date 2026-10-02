# ROSA 후속 HCL 수신계약

이 문서는 승인된 **필요한 비밀값 아닌 출력만 전달하는 경로**를 rosa HCL 구현에 연결하기 위한 계약 준비다. 실제 입력 파일·HCL 변수·Schema Validator·추출기 구현이나 A/B 수신 합의 완료를 뜻하지 않는다. 정확한 필드명·JSON/HCL 표현·파일 위치·개정 형식은 이유빈의 foundation 공통 틀과 정태훈의 소비 HCL을 대조해 후속 PR/원래 Issue에서 확정한다.

- 작성/소비: 정태훈(tjung03)
- 기반 출력 공급·foundation 전체 통합/실행 및 리뷰: 이유빈(ggbun2)
- Data 전용 의미 대조: 김상희, Registry/CI 전용 의미 대조: 최유준
- 추적: [ROSA 실행 Issue](https://github.com/seokpan/seokpan-hybrid-infra/issues/25), [기반 실행 틀 #23](https://github.com/seokpan/seokpan-hybrid-infra/issues/23)
- 2026-10-02 20:39:14 KST Source 조회 기준: Infra main `44470359c4e6de366db428adcb7831bac38a690e`, [A의 답변](https://github.com/seokpan/seokpan-hybrid-infra/issues/23#issuecomment-5951505800). 최신화는 실제 수신/Plan 직전에 다시 수행한다.

## 공급·수신 경계

State 접근 권한이 있는 Owner가 필요한 출력만 allowlist로 추출한다. 입력은 Git 밖 보호 영역에 두고 공유 기록에는 개정·논리 참조·제출/수신 범위·확인 시각을 남긴다. Account ID·실제 Role ARN·Endpoint 등의 실행값을 이 문서의 예시로 채우지 않는다. 전체 State/전체 Output 덤프·광역 `terraform_remote_state`·Token/비밀번호/Key 전달을 기본 경로로 추가하지 않는다.

A는 공통 Backend·Provider·버전·변수·출력을 작성한다. C/D의 전용 변수·출력 파일은 같은 foundation Root/State에 통합한다. B의 소비 요구를 이유로 A/C/D의 파일을 임의 중복 작성하거나 새 State로 나누지 않는다. A의 실제 인계는 받은 개정·사용 범위·보완 사유·후속 작업을 기록한 수신 판단으로 연결하며 제출·문서 병합을 수락/실행 성공으로 바꾸지 않는다.

## 필요한 필드 의미

아래 자료형은 **후속 HCL 소비에 필요한 의미**다. 실제 공급 파일의 필드 이름과 표현은 아직 합의 전이다. Data Branch의 `rds_security_group_id`·`redis_security_group_id`는 기존 이름 후보로 대조하고 최종 공통 출력 개정을 수락한다. 값이 없으면 예시 ID로 채우지 않는다.

| 입력 묶음 | 전달할 의미·소비 형태 | 공급/대조 | 소비 전 확인 |
|---|---|---|---|
| 계약/Source 개정 | 계약 및 입력 개정, 출처 Root/전체 Code SHA, 확인 시각, 기반 생성 조합/실행 기록의 논리 참조 | A 공급, B 수신 | 현재 사용 가능한 인계와 일치. 임의 TTL을 새 기준으로 정하지 않고 변경·개정 차이를 대조 |
| Account/Region | 대상 Account·서울 Region 식별 의미 | A/B 실제 Caller 대조 | 소비 Caller·Provider·Backend·선택 자원의 Account/Region 일치 |
| Backend/실행 Role | 승인 Backend 대상·rosa 정본 Key `phase2/rosa/terraform.tfstate`·목적 실행 Role 참조 | bootstrap Owner 인계, B 소비 | 별도 인증 공급, 자기 Key/Lock 범위·실제 Role·Backend/Provider 세션 일치. State 이전 반복 금지 |
| VPC/주소 | VPC ID, 승인 Machine/Service/Pod/Tunnel 등 주소 계획과 필요한 범위 | A Network 공급 | 승인 주소 계획·실제 VPC 일치 및 충돌 없음 |
| 설치 Subnet | Public 3 + ROSA Private 3의 ID 목록과 AZ/종류 매핑 | A 공급, B 소비 | 같은 VPC·서로 다른 3 AZ의 승인 역할. Data Private 3은 설치 목록에서 제외 |
| ROSA 공통 Role | 실제 Classic Account-wide Role/Policy 참조·Prefix·공유 범위 등 필요한 설치 입력 | A 공급, B 지원 Schema 대조 | 실제 Role 존재·Classic 지원·Owner/공유 영향. Cluster 종속 Operator Role/OIDC와 중복 관리하지 않음 |
| Data SG Binding | Data SG ID 각각, DB/Redis 목적·Port·허용 Source SG 의미 | C/A 공급, B 소비 | Data SG/기반 Rule은 foundation. 실제 Worker SG와 연결하는 종속 Rule은 rosa이며 inline Rule 혼용 금지 |
| ECR Worker Pull | FE/BE Repository 범위·실제 Worker Role·Pull Policy/Attachment Owner·검증 상태 | D/A 공급, B 협업 | foundation Owner 기준, 현재 미완료. CI Push Token을 Runtime Pull Secret으로 복사하지 않음 |
| 실행 조합 | 실제 지원 Classic GA 패치·Provider/도구/Lock·Quota/구독·생성/삭제 지원 조건의 근거 | B 확인, A 리뷰 | 문서 후보·Bootstrap 성공을 ROSA 지원/전체 권한 검증으로 대체하지 않음 |

App 설정용 DB/Redis Endpoint·CA·Secret 논리 참조는 그 소비 작업에서 별도 대조한다. rosa가 DB/Redis 비밀번호나 App Secret을 필요 출력으로 가져오는 구조로 확장하지 않는다. GitOps 최초 인계에 필요한 새 Cluster ID/Context·Node/Role/Host 참조는 rosa **실행 후** 확인한 범위만 후속 담당자에게 전달한다.

## Worker Pull과 SG Binding의 미완료 계약

[A의 #23 답변](https://github.com/seokpan/seokpan-hybrid-infra/issues/23#issuecomment-5951505800)은 Worker ECR Pull을 별도 미완료로 남겼다. Registry/CI 권한 PR #21 병합·Bootstrap Apply 보고와 구분한다.

- [ ] 실제 Classic Worker Role·Prefix·공유 Cluster 범위와 지원 연결 방식을 A/B가 대조했다.
- [ ] 지정 FE/BE ECR Pull Policy와 Attachment의 단일 foundation Owner·필요 출력·후속 PR 범위를 확인했다.
- [ ] rosa 서비스/기반 조회에 필요한 목적 Role 권한 차이를 A 소유 bootstrap 변경과 리뷰·인계했다. Backend 권한만으로 실행 준비가 완료됐다고 기록하지 않았다.
- [ ] Data SG 본체·기반 Rule과 rosa 종속 Binding의 Resource/Owner 경계를 확인했다.
- [ ] Cluster 삭제 전 Binding 해제·기반 보존, 새 Worker SG 연결과 foundation/rosa 양쪽 정상 Plan의 T19/IM-03 Case를 연결했다.
- [ ] 새 Worker/캐시 없는 Pull, 12시간 이상 지속 사용 뒤 새 Pull, Clean Recreate 후 Pull을 T21에 연결했다. 미수행 Case는 NOT RUN/PARTIAL로 남겼다.

현재 Policy/Attachment 구현·Apply·Pull 검증은 이 체크리스트의 작성으로 완료되지 않는다. 필요한 제품/Resource 충돌이 실제로 발견되면 승인 기준을 자동 변경하지 않고 근거·영향·변경 결정을 원래 작업 기록에 연결한다.


## Registry/CI 새 Source의 수신 조건

등록 직전의 과거 관측: [Infra PR #24](https://github.com/seokpan/seokpan-hybrid-infra/pull/24), HEAD `33432eecc6cae81f7725c9d39f8a7730ca9ce815`의 담당 4파일 제출을 확인했다. 당시 실제 Plan/Apply·권한/비용/Pull 성공은 없으며 Worker Pull은 제외된 범위였다.

당시 HEAD `33432eecc6cae81f7725c9d39f8a7730ca9ce815`에서 `registry.tf`의 untagged 7일 만료 규칙은 [Infra #18](https://github.com/seokpan/seokpan-hybrid-infra/issues/18)·[App #2 B 리뷰/수신](https://github.com/seokpan/seokpan-hybrid-app/issues/2#issuecomment-5950470901)의 **Preview 전 untagged 만료 제외·N 보류**와 달랐다. N50·7일을 당시 확정 입력으로 수락하지 않았다. PR 본문의 ECR Scan/Smoke Pull 설명도 A1의 Harbor 사본 Scan/Smoke와 당시 정합이 필요했으며 `GetDownloadUrlForLayer`는 최신 명세의 실제 E2E 필요 확인 대상으로 남겼다. 수정 개정의 준비 범위 부분 수신을 요청한 과거 인계였다. 최종 N·Lifecycle/CI 권한·Worker Pull의 확정/PASS는 해당 Preview/E2E·Role 후속 검증에 연결하며 미실행 범위를 당시 확정 입력으로 수락하지 않았다. 이 조건은 ROSA 입력계약/Source 준비를 중단시키지 않는다.

후속 단일 조회의 관측 종료는 `2026-10-02T12:12:44.366Z` / `2026-10-02T21:12:44.366+09:00`다. [Infra PR #24](https://github.com/seokpan/seokpan-hybrid-infra/pull/24)는 HEAD `4fcbab4acc3e851f9a20a9affc4e4f4c29526add`, open·미병합이며 담당 4파일과 최신 본문에서 untagged 만료 규칙/변수 및 `GetDownloadUrlForLayer` 실제 Action 제외, N=50 임시 후보·Preview 후 최종 확정, Harbor A1 Scan/Smoke 경계를 확인했다. [D의 최신 본문·검사 갱신](https://github.com/seokpan/seokpan-hybrid-infra/pull/24#issuecomment-5951983854)의 같은 HEAD init/fmt/validate 성공은 담당자 보고로 접수하며 이번에 재실행한 결과가 아니다. 옛 HEAD의 수정 지적을 현재 코드에 다시 요구하지 않는다. A의 사람 재리뷰/Merge·공통 Provider/Lock·실제 Boundary/통합 Plan/Cost·Lifecycle Preview/E2E·Worker Pull은 별도 Gate로 남긴다. 최종 N과 제외 Action의 실제 필요 여부도 실행 근거로 판단한다.

[Infra #25 Source 준비 기록](https://github.com/seokpan/seokpan-hybrid-infra/issues/25#issuecomment-5951857787)는 HCL·Schema의 정적 준비 착수를 보고한다. [Infra #26](https://github.com/seokpan/seokpan-hybrid-infra/issues/26)는 중복으로 닫혔으며 rosa 코드/입력·Plan/Cost·Runtime 정본은 [Infra #25](https://github.com/seokpan/seokpan-hybrid-infra/issues/25)다. 이 보고가 HCL 게시·정적 검사 완료·Cloud 호출/Plan/Apply를 입증하지 않는다. 보고의 AWS 6.66.0 표기는 해당 시점 기록으로 보존하고, 실제 소비 Schema·Root Lock은 승인 04와 현재 채택 AWS 6.67.0에 대조한다.

## 오류 차단과 검증 단계

| Case | 후속 구현의 기대 처리 | 지금 확인 가능한 범위 / 실제 후속 증거 |
|---|---|---|
| 필수 입력 누락·합성 ID·형식 오류 | 해당 Root 실행 입력으로 수락하지 않고 빠진 의미·제공 담당·Blocker 표시 | 계약/Case 준비. 실제 검사 코드·결과는 후속 |
| 다른 Account/Region·Root/Role/Key | 입력/Caller/Backend/Provider 불일치면 Plan/Apply 진입 차단 | 실제 Caller·보호 설정·허용/거부 결과 필요 |
| 오래된 기반 개정·변경 중 인계 | Source·생성 조합·중요 Network/Role 값 대조 후 새 입력/Plan·리뷰 | 마지막 수신 개정과 변경 영향 기록 필요 |
| 없는/다른 VPC의 Subnet·잘못된 AZ/종류 | 대상 자원 존재와 설치 선택 범위 확인 실패 시 중단 | 승인 의미 검토와 실제 읽기 조회 결과를 구분 |
| Role·Policy 미존재/미지원·공유 범위 미확인 | 실제 참조·지원·공유 영향 확인 전 해당 실행/Sync 보류 | foundation State 전체 읽기로 해결하지 않음 |
| SG Rule Owner 중복·inline 혼용 | 구현/리뷰에서 충돌 해소 전 변경 중단 | HCL/선택 Provider Schema·양쪽 Plan 및 T19 영향 대조 |
| Backend만 허용된 Role·세션 실패/만료 | 목적 Role·남은 세션·현재 Caller 재확인, 권한/인계 부족 실행 대기 | 이전 세션 또는 Admin 결과를 목적 Role의 PASS로 사용하지 않음 |
| 전체 출력/Secret·상세 보호값 로그 노출 | 추출·저장·로그 경계 검사, 보호 원본 정리/실패 기록 | 공개 기록은 논리 참조·범위·결과만 유지 |

이 표는 기대 결과와 필요한 증거의 목록이다. 검사 구현·실제 자원 조회·fmt/validate·Plan/Apply·Runtime의 PASS가 아니다. 정적 입력 검사는 실제 자원 존재·권한/통신·비용을 입증하지 않는다.

## 제출·수신·실행 Gate

1. **제출:** A가 사용할 Root/전체 SHA·입력 개정·확인 시각·생성/변경 상태·보호 논리 참조·미완료 변경/Lock·Reviewer·다음 담당을 원래 Issue의 인계 양식으로 연결한다.
2. **수신:** B가 필요한 allowlist·실제 지원 소비 Schema·받은 개정·수락/일부 수락/보완/보류 범위와 막히는 실행을 기록한다. 실제 IDs가 없는 동안 계약·HCL·Case 준비는 병행한다.
3. **Plan:** 해당 Root Code/Lock·실제 입력·Caller/MFA/목적 Role·Backend/Lock·지원/Quota·보호 Plan 경로를 확인한다. 실제 보호 Plan의 삭제/교체·권한·Data/Network 보존 영향을 A와 리뷰한다.
4. **실행:** 동일 Code/Lock/입력의 검토된 Plan, 최신 foundation 인계·필요 Secret/Pull·전체 Cost Gate·실행 창·중단/잔존 책임을 확인하고 지정 Owner가 실행한다. 최신 기반/코드 변경 시 새 Plan·리뷰로 연결한다.
5. **결과:** 실제 생성/실패/부분 자원·새 Cluster 참조·GitOps/Secret 인계 및 Runtime 시험을 별도 기록한다. 후보·Ready·인계 수락·Acceptance를 합치지 않는다.

첫 Full Apply 전 총 예상 비용은 현재 누적 + 잔여 기반/Data/Storage + ROSA Window + 전송/요청/관측 + 재시험·정리 지연/실패를 합산한다. **$450 계획선 초과 신규 가동 보류·조정, $500 전체 한도**를 유지한다. 실제 Plan/단가/Credit/시간 없이 Window 개시나 비용 PASS를 확정하지 않는다.

T19/최종 삭제 전 검증 Backup·Harbor 이미지·Render/도구·독립 사본·복호화 수단의 접근/복원 가능성을 확인한다. 기본 Destroy는 rosa State, foundation/bootstrap 전체 Destroy는 별도 명시적 승인 조건을 유지한다. 실제 삭제·종속 잔존·후속 비용과 보관/접근/보존 책임은 Owner별로 확인하고, 불필요 인증 폐기와 보존 자료 해독 Key 유지를 구분한다.

실제 기록은 [HANDOFF_TEMPLATE](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HANDOFF_TEMPLATE.md)·[Evidence 안내](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/evidence/README.md)를 따른다. 새 시험/재시험은 새 Run으로 남겨 Run Index 및 작업 Issue에 연결하고 D에게 인계한다. 충돌 가능한 실행은 WORK_TRACKER의 Shared Execution으로 조율한다. 보호 원본 실제 경로/접근 방법은 보호 운영 대장에, 공개 기록에는 논리 참조·책임·가용성/무결성 확인 결과만 연결한다.

근거: 승인03 §3-F.4~8·11·15~19, 04 §2~4·6·9~10, 개인계획 TH-10~12·16·19/§7.3·9·12, 지침 §37. 새로운 필드명·공급 형태·실제 수신 완료는 후속 합의/증거로 확정한다.


## 2026-10-02 HCL 후속 후보와 검사 범위

위 계약 준비는 [PR #27](https://github.com/seokpan/seokpan-hybrid-infra/pull/27)의 HEAD `5eaef969723e29becbb1e0611d04660ddcbdb28d`를 소비했다. [Draft PR #28](https://github.com/seokpan/seokpan-hybrid-infra/pull/28)의 `variables.tf`와 예시는 B 소비 Schema 제안이다. A/C/D가 수락한 최종 Output 표현·실제 값은 아직 아니며, 승인 의미와 기존 체크/실행 Gate를 유지한다. Schema 필드명·Review 참조의 형식 검사나 Source SHA/시각은 실제 입력 최신성·지원/권한/Cost의 증거가 아니다.

Core1.16.4/AWS6.67.0/RHCS1.7.7의 정확 제약·설치 Lock와 정적 HCL을 준비했다. 앞선 착수 댓글의 AWS 표기는 04 §8.4 채택·§8.6 후속과 정합해 6.67.0으로 정정했다. fmt·diff check·예시 JSON·공식 Source Schema 대조는 확인했으나 **Provider validate/schema 실행은 Unix socket 생성 거부로 BLOCKED**다. Controller의 같은 Source/Lock 재검증과 공급/소비 Schema 리뷰가 남는다. 설치 성공을 실제 지원·Cloud 조회·Plan/Apply나 Acceptance PASS로 올리지 않는다.

RHCS1.7.7은 삭제 timeout 후 State에서 Cluster를 제거할 수 있다. [README의 단계](README.md#worker-sg-binding과-삭제-단계)에 따라 Binding을 먼저 해제하고 `cluster_enabled=false`에서 IAM/OIDC를 유지한 채 Cluster만 제거한다. 실제 서비스 삭제 확인 후 별도 전체 rosa cleanup을 검토한다. 직접 전체 Destroy·부분 Replace의 보호는 dependency만으로 보장하지 않으며 실제 Plan/삭제 검증이 남는다. 기존 T19·최종 보존/정리 조건은 그대로 적용한다.

Registry PR #24 최신4fcb의 Source/본문 정합은 [B 후속 COMMENT](https://github.com/seokpan/seokpan-hybrid-infra/pull/24#pullrequestreview-5391575120)에서 추가 Source 요구0건으로 연결했다. 작성자 fmt/validate 보고와 A 재리뷰·실제 Boundary/통합 Plan/Preview·Worker Pull은 별도다. 이 후속 후보의 제출·정적 검사만으로 위 미완료 체크를 완료하지 않는다.

