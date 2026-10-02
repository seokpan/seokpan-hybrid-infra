# ROSA Root — 구현 준비와 실행 인계

이 Root는 **ROSA Classic Multi-AZ**를 Integration/Validation Window에 맞춰 생성·삭제한다. Terraform/RHCS가 관리하는 플랫폼과 GitOps가 관리하는 내부 선언을 분리하고, 1차 Self-managed Kubernetes와 관리 책임·지원 진단·재현성을 비교한다.

- 작성·통합 및 지정 실행 담당: 정태훈(tjung03)
- 기반 입력·Network/IAM 리뷰: 이유빈(ggbun2)
- 작업 추적: [ROSA 실행 Issue](https://github.com/seokpan/seokpan-hybrid-infra/issues/25) — 개인계획 TH-10~13·16·19 연결
- 후속 HCL 수신계약: [INPUT_CONTRACT.md](INPUT_CONTRACT.md)
- 실행 결과 정본: 해당 Issue·PR·Docs의 실제 Evidence Run. 전체 연결은 [WORK_TRACKER](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/WORK_TRACKER.md)와 [05](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/05_IMPLEMENTATION_AND_VALIDATION.md)

## 현재 확인 범위

2026-10-02 20:39:14 KST 조회 Snapshot에서 Infra main은 `44470359c4e6de366db428adcb7831bac38a690e`다. 해당 Tree의 `terraform/rosa/`에는 README만 있으며, rosa HCL·Root별 Lock·실제 Plan/Apply·Runtime 검증은 확인하지 못했다. 이 문서 추가는 구현 준비이며 실행 성공이 아니다. 작업 묶음 전후 및 공유 실행 직전에 최신 Branch·PR·댓글·검사·인계를 다시 대조한다.

[Infra #23의 이유빈 답변](https://github.com/seokpan/seokpan-hybrid-infra/issues/23#issuecomment-5951505800)은 다음 범위를 정한다.

- 이유빈이 foundation 공통 Backend·Provider·버전·변수·출력과 전체 통합·Plan·Apply를 담당한다.
- 최유준이 Registry/CI 전용 선언·변수·출력을 같은 foundation Root에 작성한다.
- Registry/CI 권한 PR #21의 병합·Bootstrap Apply **보고 확인**과 ROSA Worker Pull은 다른 항목이다.
- **Worker ECR Pull 권한은 미완료**다. 정태훈·이유빈이 실제 Worker Role·지원 연결 방식·공유 범위를 확인한 뒤 후속 PR을 조율한다.

현재 main의 `terraform/bootstrap/iam.tf`에서 rosa 실행 Role에는 자기 Backend/Lock 접근이 선언돼 있다. ROSA 서비스 작업·기반 조회 권한까지 갖췄다고 판단하지 않는다. 필요한 차이는 승인된 목적 Role 모델 안에서 bootstrap 소유 변경과 조율하며, 개인 MFA 또는 Bootstrap 점검 성공을 rosa Plan 권한의 증거로 대체하지 않는다.


## Registry/CI 새 Source의 수신 조건

등록 직전 [Infra PR #24](https://github.com/seokpan/seokpan-hybrid-infra/pull/24), HEAD `33432eecc6cae81f7725c9d39f8a7730ca9ce815`의 담당 4파일 제출을 확인했다. 실제 Plan/Apply·권한/비용/Pull 성공은 없으며 Worker Pull은 이 PR에서도 제외되어 있다.

현재 `registry.tf`의 untagged 7일 만료 규칙은 [Infra #18](https://github.com/seokpan/seokpan-hybrid-infra/issues/18)·[App #2 B 리뷰/수신](https://github.com/seokpan/seokpan-hybrid-app/issues/2#issuecomment-5950470901)의 **Preview 전 untagged 만료 제외·N 보류**와 다르다. N50·7일을 확정 입력으로 수락하지 않는다. PR 본문의 ECR Scan/Smoke Pull 설명도 A1의 Harbor 사본 Scan/Smoke와 정합이 필요하며 `GetDownloadUrlForLayer`는 최신 명세의 실제 E2E 필요 확인 대상으로 유지한다. 담당자가 코드/설명을 정합한 개정의 준비 범위는 부분 수신할 수 있다. 최종 N·Lifecycle/CI 권한·Worker Pull의 확정/PASS는 해당 Preview/E2E·Role 후속 검증에 연결하며 미실행 범위를 확정 입력으로 수락하지 않는다. 이 조건은 ROSA 입력계약/Source 준비를 중단시키지 않는다.

## 승인 기준과 소유권

| 대상 | 기준·Owner |
|---|---|
| Backend | 서울 `ap-northeast-2`, 정본 Key `phase2/rosa/terraform.tfstate`, S3 Lock·암호화. Bucket/실행 Role은 bootstrap 소유 |
| Cluster | Public API/Ingress의 ROSA Classic Multi-AZ, Cluster·Machine Pool·Cluster 종속 Operator Role/OIDC는 rosa |
| 기반 | VPC·Subnet·Route·NAT·Data SG 본체/기반 Rule·RDS·Redis·ECR·Backup S3·Account-wide Role/Policy는 foundation |
| Data 접근 Binding | 실제 Worker Source SG → Data SG의 종속 Rule만 rosa. SG inline Rule과 별도 Rule 혼용·중복 Owner 금지 |
| ECR Worker Pull | 프로젝트 Pull Policy·실제 Classic Worker Role Attachment는 foundation. rosa는 필요한 참조를 소비하고 실제 Pull 검증에 연결 |
| 내부 Desired State | Namespace·AppProject·App·Route·ConfigMap·관측 선언은 GitOps. 이 Root에서 상시 중복 관리하지 않음 |
| 최초 GitOps 인계 | Infra 최소 Ansible이 설치/Root 최초 등록, 이후 원본은 GitOps. App는 Secret/Schema/Pull 준비 전 Sync 보류 |
| Secret | 별도 SOPS+age 공급. RHCS 인증은 `RHCS_TOKEN` 환경변수로 주입하며 값·Token·State/Plan을 Git/공개 로그에 기록하지 않음 |

승인된 초기 구성은 Control Plane 3·Infrastructure 3·Worker 3이며 Worker는 `m5.xlarge` 후보다. 서비스 지정 Control Plane/Infrastructure 사양·EBS를 Worker와 같다고 가정하지 않는다. 설치 Subnet은 **Public 3 + ROSA Private 3**이며 Data Private 3개는 설치 입력이 아니다. 실제 AZ·지원·Quota·구독·제공 GA 패치는 실행 입력이다. 기존 README의 vCPU 100 표기를 현재 Account 가용량 또는 충분한 Quota의 증거로 사용하지 않는다.

버전 준비 기준은 Terraform Core `1.16.4`, 현 Infra 채택 AWS Provider `6.67.0`, RHCS `1.7.7` 초기 후보, ROSA `4.20` 계열의 제공 GA 패치 후보다. 실제 rosa Schema·지원 조합·Root별 `.terraform.lock.hcl`을 확인한다. Bootstrap의 Lock/검증을 rosa 검증으로 대체하거나 무조건 `init -upgrade`하지 않는다.

## 구현·리뷰·실행 완료 조건

각 체크는 해당 Source/입력 개정과 결과 링크가 있을 때만 갱신한다. 코드 병합·인계 수락·실행·최종 시험은 따로 판정한다.

- [ ] **TH-10 입력 리뷰:** A의 제한 출력·공통 파일 개정, C의 Data SG 의미, D의 ECR/Pull 참조를 대조하고 사용할 범위·남은 Blocker를 원래 작업 기록에서 수락한다.
- [ ] **TH-10 HCL 준비:** 승인 Owner·정본 Key·지원 Schema/버전·오류 차단을 코드로 대응하고 fmt/validate·A 리뷰를 연결한다. 필요한 서비스 권한 변경은 bootstrap Owner와 조율한다.
- [ ] **TH-11 관리 인계:** 정상 개인 IDP/RBAC·Argo 권한·유지 비상 경로와 Cloud Secret 주/예비·공급/독립 사본을 확인한다. 정상/비상 경로 시험 후 초기 높은 인증·신규/기존 Token·Argo Session 회수와 잔존 결과를 기록한다.
- [ ] **TH-12 실제 Plan:** 최신 기반 인계·실제 입력·Caller/MFA/목적 Role·Backend/Lock·도구/Lock·지원/Quota와 보호 Plan 경로를 확인한 뒤 Root 전체 Plan을 생성한다.
- [ ] **TH-12 리뷰/Cost:** A가 Network/IAM·삭제/교체·종속 영향과 실제 Plan을 검토한다. D에게 전체 구성·생성/가동/삭제/재시험·잔존 입력을 인계하고 전체 Cost Gate·실행 창을 연결한다.
- [ ] **TH-13 실행:** 리뷰된 동일 Code/Lock/입력·Plan, 최신 foundation 인계·Secret/Pull 준비·실행 창에서 지정 실행자가 진행한다. 기반/Source 변경은 새 Plan·리뷰로 연결한다.
- [ ] **TH-13 통합:** Ready·관리 접속과 GitOps 인계·실제 FE/API/WSS·DB/Redis·업무·정상 Baseline·Backup/Release를 각각 확인한다. Apply 성공만으로 Acceptance를 PASS 처리하지 않는다.
- [ ] **TH-16/T19 재생성:** 아래 보존·Binding 해제·새 Worker SG 연결·양쪽 정상 Plan·업무 재현과 실제 증거를 연결한다.
- [ ] **TH-19 최종 정리:** 별도 삭제/보존 범위·Owner·접근/복원 가능성·실제 잔존/후속 비용·인증 폐기와 보존 Key 유지 결과를 확인한다.

독립적인 입력계약·Case·코드 준비는 App/base·lab 준비와 병행한다. 필요한 입력이 없는 Plan·실제 연결·유료 실행만 대기로 남긴다. 같은 State 쓰기·공유 Context 변경·Restore·장애/부하 실행은 지정 실행자와 [Shared Execution](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/WORK_TRACKER.md#shared-execution)에서 조율한다.

## 비용·삭제·Clean Recreate

**첫 Full Apply 전 전체 Cost Gate**를 확인한다. 현재 누적 + 잔여 기반/Data/Storage + ROSA 관련 Window + 전송/요청/관측 + 재시험·정리 지연/실패 비용을 합산한다. $450 계획선을 넘으면 신규 가동을 보류·조정하고 $500 전체 한도를 유지한다. 9개 Node·ROSA 서비스·LB·NAT·EBS·Window 사이 잔존을 포함하며 실제 단가/Credit/시간 없이 비용 PASS나 가동일을 확정하지 않는다.

T19 재생성은 다음 순서를 확인한다.

1. 삭제 전 foundation 재실행 전후 기존 rosa Binding 유지·Rule 덮어쓰기/영구 Diff 없음의 실제 결과를 확인한다.
2. App 쓰기 제한·진행 상태·최신 Backup 로컬 확보·남은 Data/Network 의존성과 검증 Harbor Image·Render·도구·Secret/Key·독립 사본의 접근/복원 가능성을 확인한다.
3. rosa의 Worker→Data SG 종속 Binding을 해제하고 Cluster·종속 자원 삭제를 조율한다. Binding 해제 뒤 foundation의 Data SG/기반 Rule·Data·Network와 bootstrap 소유 Backend의 보존을 확인한다.
4. 새 Cluster의 실제 Worker SG·Role/OIDC·Host·Context를 확인하고 Binding·Pull·GitOps·Secret 재주입과 대표 업무를 재현한다. 이전 Worker SG 참조 잔존 없음도 확인한다.
5. foundation/rosa 양쪽 정상 Plan과 T19 업무 재현 결과를 확인한다. 각 변경과 실패/재시험을 별도 Run에 남긴다.

C가 RDS Stop을 선택한 경우에만 Client 종료→Stop 완료와 다음 실행 전 Start·연결·Data 확인을 인계한다. 모든 rosa 삭제에 RDS Stop을 강제하지 않는다. App/Data 보호 조건은 최종 rosa 삭제에도 적용한다.

최종 종료는 T19의 재생성 성공과 별도다. 기본 비용절감 Destroy는 **rosa State**이며 foundation/bootstrap 전체 Destroy는 별도 명시적 승인 조건을 유지한다. 삭제 요청과 실제 삭제·종속 Role/OIDC/LB/Volume 등의 잔존·후속 청구 확인을 구분하고 자원별 Owner·기간·보존 비용을 인계한다. 불필요 Credential/Token·시험 Secret 폐기와 보존 자료의 복호화 Key 유지를 구분한다. 전체 자원/비용이 0이라고 확인 없이 기록하지 않는다.

## 기록과 근거

실제 제출·수신·Blocker는 [HANDOFF_TEMPLATE](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HANDOFF_TEMPLATE.md)에 따라 원래 Issue에, 코드/검사는 PR에, 실제 실행/실패/재시험은 Docs `evidence/<test-id>/<run-id>/`와 Run Index에 연결한다. 실행자와 Caller, 배정 담당과 Reviewer를 구분한다. 공개 기록은 논리 참조와 결과만, 실제 보호 경로·접근/보존 책임은 보호 운영 대장에서 관리한다. 05/WORK_TRACKER에는 원본 링크·상태·영향을 연결하고 D의 Index 작업에 인계한다.

- [03 상세설계](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/design/03_DETAILED_DESIGN.md): §3-F.4~8·15~19, §3-G의 T02~04·T19~21·T23, §3-H Cost/Window
- [04 구현 준비](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/design/04_IMPLEMENTATION_READINESS.md): §2~6·9~10
- [팀 작업·인계 가이드](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/TEAM_WORK_AND_HANDOFF_GUIDE.md): 정태훈 담당·공유 실행·정리
- 개인계획 TH-10~13·16·19와 §7.3·9·12, PROJECT_INSTRUCTIONS §37
