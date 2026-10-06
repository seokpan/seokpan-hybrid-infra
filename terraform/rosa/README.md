# ROSA Classic Root — TH-10 Source candidate

원본 작업은 [Infra #25](https://github.com/seokpan/seokpan-hybrid-infra/issues/25)다. [PR #27](https://github.com/seokpan/seokpan-hybrid-infra/pull/27)의 [제한 입력·실행 인계 계약](INPUT_CONTRACT.md)을 소비한 별도 HCL 후속 PR이다. 작성/지정 실행은 정태훈, 기반 Network/IAM 리뷰는 이유빈이며 Data/Registry 전용 의미는 C/D와 대조한다. TH-11 관리·TH-12/13 실행·TH-16 재생성·TH-19 최종 보존/정리 책임은 원본 Issue와 계약에 유지한다. 공개 승인 [03 §3-F](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/design/03_DETAILED_DESIGN.md)와 [04](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/design/04_IMPLEMENTATION_READINESS.md)를 정적 Source로 연결한다. 개인 계획 TH-10은 별도 제공된 Project Source이며 공개 저장소에 게시되지 않은 근거다. 실제 foundation 입력·IAM 서비스 권한·Cloud Plan/Apply·Cost Gate·ROSA 생성은 아직 수행하지 않았다. 예시의 `INPUT_REQUIRED`를 임의 값으로 바꿔 실행하지 않는다.

실제 첫 Plan 전 본인 Source/도구·보호 입력·Backend/Caller의 단계별 준비는 [로컬 준비 절차](LOCAL_PREPARATION.md)를 따른다. 기존 도구만 사용하며 오프라인 확인과 실제 인증/Cloud 실행을 구분한다.

## 범위와 Owner

| 대상 | Owner / 구현 |
|---|---|
| Backend·TF Role | 기존 bootstrap. rosa는 기존 Bucket과 `seokpan-tf-rosa` 세션을 소비 |
| Network·Data·Account-wide ROSA Role/공통 정책 | foundation/A 통합. rosa는 제한 입력만 소비 |
| Worker ECR Pull Policy·Classic Worker Role Attachment | foundation/A·D 후속. rosa에서 중복 선언하지 않음 |
| Cluster·managed OIDC config/provider·Operator Role/Attachment | rosa/B. RHCS는 OCM Config/Cluster, AWS Provider는 고객 IAM Provider·Role/Attachment를 관리. Operator Data Source는 목록 조회만 수행. [실제 객체별 경계](REVIEW_AND_EXECUTION_GATES.md#객체별-실제-provider-소유권) |
| Worker→Data SG Binding | rosa/B. Data SG 본체·기반 Rule은 foundation. inline Rule 혼용 금지 |
| Namespace·GitOps/App/IDP·Secret | 승인된 GitOps/최소 Bootstrap/별도 공급. rosa에 생성하지 않음 |

Root는 `bootstrap/foundation/rosa` 3개다. State Key는 **`phase2/rosa/terraform.tfstate`**, 서울 Region·암호화 요청·S3 native Lock을 사용한다. 기존 State 이전을 반복하지 않는다. Backend Bucket은 Git 밖 보호된 `-backend-config`로 공급한다. Backend/Provider가 같은 지정 목적 Role을 사용하는지는 실행 전에 각각 확인한다.

## 고정 조합과 초기 Worker

- Terraform Core **1.16.4**, `hashicorp/aws` **6.67.0**, `terraform-redhat/rhcs` **1.7.7** 정확 제약과 실제 다운로드 Lock을 둔다. AWS는 04 §8.4의 채택값이며 §8.6의 정확 제약/Lock 후속과 연결한다.
- Classic STS·기존 VPC·Public API/Ingress·Multi-AZ를 선언한다. **Public 3 + ROSA Private 3 = 6 Subnet**이며 Data Private 3는 설치 목록에 넣지 않는다. CIDR/AZ/VPC 조합을 Data Source에서 대조한다. 실제 Route/NAT/Endpoint는 별도 Gate다.
- 기본 `worker` pool의 **총 Worker 수 3**, AZ별 1개 목표·`m5.xlarge` 후보·자동 확장 비활성화다. 별도 3개 pool을 더하지 않는다. CP 3+Infra 3을 포함한 최소 9 EC2와 부속 비용을 전체 Cost Gate에서 검토한다.
- VPC/Machine `192.168.64.0/20`, Pod `10.128.0.0/14`, Service `10.240.0.0/16`이다. 정확한 지원 `4.20.z` GA patch·Worker disk size는 실제 조회/비용 검토 후 입력한다.
- RHCS의 기본 Machine Pool 생성 시 입력과 Day 2 관리는 다르다. 생성 후 pool 변경은 **현재 default pool을 같은 rosa State로 import하고 리뷰한 개정**으로 수행한다. 자동 import·추가 pool은 이번 Source에 없다.
- `create_admin_user=false`다. TH-11의 초기 관리·정상 IDP/RBAC·유지 비상 관리자·회수는 별도 구현이다. 이 Source만으로 전체 Clean Recreate가 완성되지 않는다. Token/관리 비밀번호를 TF 변수/State에 추가하지 않는다.

TH-11은 정상 개인 IDP/RBAC·Argo 권한·유지 비상 경로와 Cloud Secret 주/예비·독립 사본을 확인한 뒤 초기 높은 인증·신규/기존 Token·Argo Session 회수와 잔존을 시험한다. TH-12의 실제 보호 전체 Plan은 A가 Network/IAM·삭제/교체·종속 영향을 리뷰하고 D에게 전체 비용/실행 창을 인계한다. 충돌 실행은 WORK_TRACKER Shared Execution에서 조율하며 Owner·작성 지원·실제 실행자/Caller·Reviewer를 구분한다.

## foundation 입력 계약 제안

`variables.tf`의 `foundation`은 **B 소비 Schema 제안**이다. A/C/D 수신·실제 Output 구현은 미완료다. A가 권한 있는 사본에서 필요한 Output만 추출해 Git 밖 보호 입력으로 제공한다. 전체 Output dump·`terraform_remote_state`를 추가하지 않는다.

| 필드 | 의미 / 공급 확인 |
|---|---|
| schema/revision/source SHA/confirmed_at/generation versions | 입력 개정·foundation Code 전체 SHA·RFC3339 시각·생성 조합 |
| environment/account_id/region/vpc_id | cloud·동일 Account·서울·승인 VPC |
| subnets.az_a/az_b/az_c | 실제 AZ와 Public/ROSA Private ID pair. 슬롯은 A #23의 의미와 연결하며 실제 AZ letter는 입력 |
| account_role_prefix/iam_path/account_roles | 실제 Classic Installer/Support/ControlPlane/Worker ARN·공유 범위·Policy/Trust/PassRole |
| operator_policy_arns | RHCS 실제 `policy_name`→공통 Classic 정책 ARN Map. 정책 본체는 foundation 전제, 누락 시 중단 |
| optional boundary/external ID | 해당 계정의 실제 Role 제약에 맞는 값. 임의 Boundary 추가/제거 금지 |
| data_security_groups | 서로 다른 실제 MariaDB/Redis SG ID·기반 Rule·다른 Owner 확인 |

Schema는 다른 Region/환경·중복 Subnet·3 AZ 불일치·다른 Account Role/정책 ARN·잘못된 개정/버전 형식·계열을 차단한다. 실제 read-only Data Source는 Caller·VPC CIDR/DNS·Subnet CIDR/VPC/AZ·Private IP 설정·Role ARN을 대조한다. **Output 최신성·Route/NAT/Endpoint·IAM Policy/Trust 권한·지원/Quota·Cost는 이 검사로 입증하지 않는다.** 최신 source/output 개정·생성 조합·자원/Owner를 별도로 확인하고 기반 변경 뒤 다시 추출/리뷰한다.

공식 Classic module은 Operator Role/Attachment 뒤 20초, OIDC 생성/삭제에 10초 대기를 사용한다. 이번 direct-resource Source에는 고정 대기를 추가하지 않았다. **실제 IAM/OIDC 전파와 생성 재시도 처리도 미검증 Gate**이며 dependency만으로 전파 성공을 주장하지 않는다. 시간 경과만으로 전파를 입증할 수도 없다. 실제 Plan/첫 생성 전 지원 경로·재실행/부분 실패 처리를 검증한다.

## OIDC issuer 소비와 리뷰 판단

[현재 C 리뷰의 형식·소유권·검사/실행 순서](REVIEW_AND_EXECUTION_GATES.md#2026-10-05-c-리뷰--oidc-형식과-실제-실행-순서)를 따른다. 고정 RHCS 1.7.7은 `oidc_endpoint_url`에 scheme을 제거한 host/path를 반환하므로 기존 Trust가 실제로 깨졌다고 가정하지 않는다. 소비 코드도 `trimprefix`로 이를 명시하고 AWS Provider URL과 Trust의 `sub` key에 같은 host/path를 사용한다. 실제 생성 Trust JSON·URL은 두 issuer 형태의 격리 Source harness/mock Provider plan으로 검사한다. 이는 실물 STS Federation 시험이 아니며, 준비 단계의 실제 issuer/IAM/지원 대조와 생성 후 실제 Operator의 WebIdentity/STS 경로를 따로 확인한다. 기존 Root/State/Policy Owner·Provider/Lock·삭제 경계는 유지한다.

이후 [A의 최신 HEAD 변경 요청](https://github.com/seokpan/seokpan-hybrid-infra/pull/28#pullrequestreview-5414713742)을 반영하여 `sub` 조건 연산자는 `ForAnyValue:StringEquals`에서 plain `StringEquals`로 바꾼다. 허용 ServiceAccount 목록은 그대로 두고 단일 요청 `sub`를 그 목록과 비교하며, 두 issuer 형태 시험도 생성된 Trust JSON의 정확한 `StringEquals`와 기존 6개 Role/Attachment 대응을 확인한다. [이번 판단과 보존 범위](REVIEW_AND_EXECUTION_GATES.md#2026-10-05-a-후속-리뷰--sub-조건-연산자)를 따른다. 기존 고정 Source의 실제 무단 접근·STS 실패가 관측됐다고 표현하지 않으며 최신 HEAD의 실제 CI와 사람 재리뷰로 Source 수락을 판단한다.

## Worker SG Binding과 삭제 단계

1. `cluster_enabled=true`, `worker_sg_binding=null`으로 최초 Cluster를 준비한다. Data ingress Rule은 0개다. Ready는 App/Data 연결 완료가 아니다.
2. B가 현재 Cluster·infra ID·실제 service-created Worker SG와 Node/ENI 연결을 확인해 제한된 `worker_sg_binding`을 공급한다. 같은 VPC의 임의 SG를 선택하거나 추측 Tag로 자동 조회하지 않는다.
3. 새 전체 Plan에서 SG VPC·현재 Cluster ID·서로 다른 C/A Data SG를 확인해 MariaDB 3306/Redis 6379의 SG→SG Rule 2개만 추가한다. **Cluster ID/VPC 검사만으로 Worker SG 소속/ENI가 입증되지는 않는다.** 실제 Rule 충돌·허용/거부·foundation 재실행 전후 Binding 유지·양쪽 정상 Plan은 별도 Acceptance다.
4. 삭제 전 App 쓰기/Client·검증 Backup/Bundle 접근·Evidence 보존을 확인한다. 먼저 `worker_sg_binding=null` 개정의 전체 Plan에서 Binding만 제거하고 적용한다.
5. 이어 `cluster_enabled=false` 개정의 전체 Plan에서 Cluster 삭제·IAM/OIDC **유지**를 확인한 뒤 Cluster를 제거한다. 실제 Red Hat Cluster 삭제 완료 및 AWS 정리를 확인하기 전 IAM/OIDC cleanup을 진행하지 않는다. `-target`을 안전한 삭제 절차로 제시하지 않는다.
6. **RHCS 1.7.7은 삭제 timeout에 Warning 후 State에서 Cluster를 제거할 수 있다.** State absence나 명령 성공만으로 실제 삭제 완료를 판단하지 않는다. Warning/실제 서비스·자원 상태를 확인하고 불완전 삭제는 IAM/OIDC를 유지한 채 중단·보호 기록·지원/State 정합으로 처리한다. Source만으로 자동 실패 복구가 완성된 것은 아니다.
7. 실제 Cluster 삭제가 확인된 후 승인된 rosa 전체 cleanup을 Plan/리뷰한다. foundation Data/기반 Rule/Network·bootstrap Backend는 보존한다. 전체 foundation/bootstrap Destroy는 별도 명시 승인 조건을 유지한다. 실제 Orphan/잔존/후속 청구는 따로 확인한다.
8. 재생성 전 옛 Binding을 제거하고 새 Cluster/SG·근거를 공급한다. `binding_enabled`는 선언 상태이며 통신 PASS가 아니다. 이 단계들은 같은 rosa Root/State다.

C가 RDS Stop을 선택한 경우에만 Client 종료→Stop 완료와 다음 실행 전 Start·연결·Data 확인을 인계한다. 모든 rosa 삭제에 RDS Stop을 강제하지 않는다. 불필요 Credential/Token·시험 Secret 정리와 보존 자료의 복호화 Key 유지도 구분한다.

살아 있는 Cluster에 **곧바로 전체 Root Destroy를 실행하면 timeout 이후 IAM/OIDC가 먼저 사라질 수 있으므로 사용하지 않는다.** 두 단계의 실제 보호 Plan/삭제 결과와 Provider timeout 처리는 실행 전 검증한다.

Cluster의 immutable 필드 변경으로 **부분 Replace**가 나타난 경우에도 현재 Binding을 유지한 채 실행하지 않는다. Rule의 `depends_on`은 기존 Rule을 새로운 Worker SG로 자동 이전하거나 부분 Replace 전에 삭제한다고 보장하지 않는다. 위 Binding 해제·Cluster 제거·실제 삭제 확인·재생성·새 Binding 순서를 별도로 Plan/리뷰한다. IAM/OIDC의 교체도 같은 원본/서비스 수명과 대조한다.

## 정적 검사와 실행 경계

Root에서 아래 정적 명령을 수행한다. Provider 다운로드와 State 초기화·Cloud Plan은 구분한다.

```bash
terraform version
terraform fmt -check
terraform init -backend=false -input=false -lockfile=readonly
terraform validate
```

Provider Schema는 아래 GitHub 검사 또는 [원래 Issue #25](https://github.com/seokpan/seokpan-hybrid-infra/issues/25)의 Backend 제외 임시 사본 순서를 사용한다. 원래 S3 선언 Root에서 `init -backend=false`만 한 뒤 직접 Schema를 조회하면 Backend 초기화 요구로 중단될 수 있다. 이 정적 검사를 위해 실제 S3 Backend를 초기화하지 않는다.

**기존 로컬 환경의 결과:** Core 1.16.4 공식 checksum·공개 Provider 설치/서명·Root Lock·fmt 확인. `validate`/schema는 Provider RPC의 Unix socket 생성이 `operation not permitted`로 차단되어 **미통과**다. 권한을 우회하지 않았으며 다른 Controller에서 같은 Source/Lock로 재검증해야 한다. 공식 v1.7.7 Source Schema 대조는 실행된 Provider Schema/validate PASS가 아니다.

**후속 GitHub Linux 결과:** 2026-10-05의 문서 보완 전 HEAD `b65dc9244f1d6714c4dc56cba4267b572027f661`에서 [Source Run 37296404096](https://github.com/seokpan/seokpan-hybrid-infra/actions/runs/37296404096)이 success다. 고정 조합·fmt·원 Root validate(errors=0/warnings=0)·실제 Provider Schema의 선언 Type 13개·Source/Lock 불변을 확인했다. 위 로컬 BLOCKED는 과거 환경의 이력으로 보존한다. 새 HEAD에는 새 실제 검사 결과를 연결하며, 이 정적 PASS를 실행 Controller/Caller·Backend/IAM/지원·실제 Plan/Apply의 성공으로 사용하지 않는다.

예시는 의도적으로 실행 불가다. 실제 입력/Backend/Plan은 Git 밖 보호 영역, RHCS 인증은 `RHCS_TOKEN` 환경변수로 공급한다. 초기 input/support/예비 비용·Window 리뷰로 Owner의 첫 Plan을 준비하고, 생성 전 **실제 보호 전체 Plan의 최신 수량·누적/잔존·시간·Cost Gate와 팀 리뷰**를 확인한다. `execution_review` 문자열은 보조 검사이며 성공 증거/Apply 승인을 발급하지 않는다. `$450` 계획선 초과 신규 가동 보류·조정, 총 `$500` 한도를 유지한다. 이번 작업에는 Cloud 조회/Plan/Apply·유료 호출이 없다.

## GitHub Linux Source 검사

[ROSA source validation Workflow](../../.github/workflows/rosa-static-validation.yml)는 TH-10의 Source/Lock 정적 검사다. 표준 `ubuntu-24.04`에서 제출 Commit을 checkout하고 공식 SHA-256으로 확인한 Core 1.16.4·고정 AWS 6.67.0/RHCS 1.7.7을 사용한다. 별도 TF_DATA_DIR/CLI config·읽기 전용 Lock·Backend 비활성화로 fmt(테스트 파일 포함)→init→실제 버전→validate JSON→실제 Provider Schema와 선언 Type→두 issuer 형태의 mock Provider plan→Source/Lock 불변을 확인한다. 원래 Root의 validate는 Backend 선언을 포함한 원문 그대로 수행한다. `providers schema`는 Backend/State를 먼저 읽으므로, 그 명령만 같은 HCL/Lock에서 `backend.tf`를 제외한 임시 사본·별도 TF_DATA_DIR로 조회한다. 나머지 HCL/Lock 사본의 동일성을 대조하며 실제 Root Backend 초기화·State/입력의 검증 결과로 확대하지 않는다. OIDC mock 테스트는 별도 격리 Source harness에서 수행한다. production oidc.tf의 OIDC/Role/Attachment/정규화 선언을 보존해 대조하고, mock이 지원하지 않는 Operator Data Source만 생략해 그 목록 참조를 합성 6개 입력으로 치환한다. variables/providers/versions/Lock은 원본을 복사하고 input_contract는 harness 한정 stub이다. AWS/RHCS를 모두 mock한 두 plan으로 실제 생성 URL·Trust JSON·Attachment를 확인한다. 실제 foundation 조회·Operator 목록 조회/6개 postcondition·Cluster graph를 이 harness로 시험한 것으로 기록하지 않는다. 실제 Secret/OIDC·tfvars/Backend config·TF 세션·Cloud Plan/Apply를 공급하지 않으며 D의 Jenkins App Build/Scan/Promotion과 별도다.

Draft PR도 검사하며 PR의 실제 HEAD SHA를 사용한다. PR/해당 작업 Branch·main의 ROSA/Workflow 변경만 실행하고 같은 Commit 중복 실행은 취소한다. 저장소 정책·필수 체크·리뷰 규칙은 변경하지 않는다. GitHub Actions Run의 실제 결론과 같은 Commit의 로그를 원래 Issue/PR에 연결하며 Workflow 게시 자체를 PASS로 기록하지 않는다. 기존 로컬 RPC BLOCKED는 당시 환경 이력으로 유지한다. GitHub Linux 정적 검사 통과도 실제 실행 Controller/Caller·Input/IAM/지원·Plan/Cost·Cloud/Recovery Acceptance를 대신하지 않는다. 전체 schema/캐시 Artifact를 업로드하지 않고 로그에는 검증 결과·필요 Type·Source/Lock/버전만 남긴다.

## 공식 자료와 남은 Gate

- [Red Hat Classic Terraform 절차](https://docs.redhat.com/en/documentation/red_hat_openshift_service_on_aws_classic_architecture/4/html/install_rosa_classic_clusters/creating-a-red-hat-openshift-service-on-aws-classic-architecture-cluster-with-terraform)
- [RHCS v1.7.7 Classic Schema](https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/docs/resources/cluster_rosa_classic.md)
- [RHCS v1.7.7 Default Worker pool](https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/docs/guides/worker-machine-pool.md)
- [RHCS v1.7.7 managed OIDC](https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/docs/resources/rosa_oidc_config.md)
- [공식 Classic v1.7.2 Operator Role/Trust/전파](https://github.com/terraform-redhat/terraform-rhcs-rosa-classic/blob/v1.7.2/modules/operator-roles/main.tf)
- [RHCS v1.7.7 실제 Delete 구현](https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/provider/clusterrosa/classic/cluster_rosa_classic_resource.go)

Controller 재검증·A/C/D Schema 수신·실제 Output/Route/Role/Policy·서비스 권한 PR·전체 Plan/Cost/Window·전파/부분 실패·Cluster/SG Binding·Node ECR Pull·TH-11 관리/Secret/GitOps·업무/Migration·T19 Recreate·T20/T23 정리가 남아 있다. 실제 CLI/Controller 버전·지원 조합도 실행 시 확인한다. 결과는 Issue/PR 원본에 기록하고 공개 [WORK_TRACKER](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/WORK_TRACKER.md)·[05](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/05_IMPLEMENTATION_AND_VALIDATION.md)에는 원본 링크·범위·남은 Gate를 연결한다. 역할/인계는 [팀 작업·인계 가이드](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/TEAM_WORK_AND_HANDOFF_GUIDE.md)를 따르며 비공개 개인 계획이나 지침 사본을 공개 근거로 제시하지 않는다.


## Source 리뷰와 실제 실행의 분리

[리뷰/실행 조건과 인계](REVIEW_AND_EXECUTION_GATES.md)를 사용한다. 최신 main·Source/Lock 검사를 확인한 뒤 사람 Source 리뷰를 시작할 수 있다. 실제 입력 수신·Controller/권한/지원·첫 Plan 준비·전체 Plan/Cost/유료 실행·Runtime 수락은 원 Issue #25에서 별도 확인한다. Source 병합으로 실제 실행 Gate나 TH 전체 완료를 해제하지 않는다.
