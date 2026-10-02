# ROSA Classic Root — TH-10 Source candidate

원본 작업은 [Infra #25](https://github.com/seokpan/seokpan-hybrid-infra/issues/25)다. 승인 03 §3-F와 04·개인 계획 TH-10을 정적 Source로 연결한다. 실제 foundation 입력·IAM 서비스 권한·Cloud Plan/Apply·Cost Gate·ROSA 생성은 아직 수행하지 않았다. 예시의 `INPUT_REQUIRED`를 임의 값으로 바꿔 실행하지 않는다.

## 범위와 Owner

| 대상 | Owner / 구현 |
|---|---|
| Backend·TF Role | 기존 bootstrap. rosa는 기존 Bucket과 `seokpan-tf-rosa` 세션을 소비 |
| Network·Data·Account-wide ROSA Role/공통 정책 | foundation/A 통합. rosa는 제한 입력만 소비 |
| Worker ECR Pull Policy·Classic Worker Role Attachment | foundation/A·D 후속. rosa에서 중복 선언하지 않음 |
| Cluster·managed OIDC config/provider·Operator Role/Attachment | rosa/B. 동일 객체의 AWS/RHCS/CLI 중복 관리 금지 |
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

## Worker SG Binding과 삭제 단계

1. `cluster_enabled=true`, `worker_sg_binding=null`으로 최초 Cluster를 준비한다. Data ingress Rule은 0개다. Ready는 App/Data 연결 완료가 아니다.
2. B가 현재 Cluster·infra ID·실제 service-created Worker SG와 Node/ENI 연결을 확인해 제한된 `worker_sg_binding`을 공급한다. 같은 VPC의 임의 SG를 선택하거나 추측 Tag로 자동 조회하지 않는다.
3. 새 전체 Plan에서 SG VPC·현재 Cluster ID·서로 다른 C/A Data SG를 확인해 MariaDB 3306/Redis 6379의 SG→SG Rule 2개만 추가한다. **Cluster ID/VPC 검사만으로 Worker SG 소속/ENI가 입증되지는 않는다.** 실제 Rule 충돌·허용/거부·foundation 재실행 전후 Binding 유지·양쪽 정상 Plan은 별도 Acceptance다.
4. 삭제 전 App 쓰기/Client·검증 Backup/Bundle 접근·Evidence 보존을 확인한다. 먼저 `worker_sg_binding=null` 개정의 전체 Plan에서 Binding만 제거하고 적용한다.
5. 이어 `cluster_enabled=false` 개정의 전체 Plan에서 Cluster 삭제·IAM/OIDC **유지**를 확인한 뒤 Cluster를 제거한다. 실제 Red Hat Cluster 삭제 완료 및 AWS 정리를 확인하기 전 IAM/OIDC cleanup을 진행하지 않는다. `-target`을 안전한 삭제 절차로 제시하지 않는다.
6. **RHCS 1.7.7은 삭제 timeout에 Warning 후 State에서 Cluster를 제거할 수 있다.** State absence나 명령 성공만으로 실제 삭제 완료를 판단하지 않는다. Warning/실제 서비스·자원 상태를 확인하고 불완전 삭제는 IAM/OIDC를 유지한 채 중단·보호 기록·지원/State 정합으로 처리한다. Source만으로 자동 실패 복구가 완성된 것은 아니다.
7. 실제 Cluster 삭제가 확인된 후 승인된 rosa 전체 cleanup을 Plan/리뷰한다. foundation Data/기반 Rule/Network·bootstrap Backend는 보존한다. 전체 foundation/bootstrap Destroy는 별도 명시 승인 조건을 유지한다. 실제 Orphan/잔존/후속 청구는 따로 확인한다.
8. 재생성 전 옛 Binding을 제거하고 새 Cluster/SG·근거를 공급한다. `binding_enabled`는 선언 상태이며 통신 PASS가 아니다. 이 단계들은 같은 rosa Root/State다.

살아 있는 Cluster에 **곧바로 전체 Root Destroy를 실행하면 timeout 이후 IAM/OIDC가 먼저 사라질 수 있으므로 사용하지 않는다.** 두 단계의 실제 보호 Plan/삭제 결과와 Provider timeout 처리는 실행 전 검증한다.

Cluster의 immutable 필드 변경으로 **부분 Replace**가 나타난 경우에도 현재 Binding을 유지한 채 실행하지 않는다. Rule의 `depends_on`은 기존 Rule을 새로운 Worker SG로 자동 이전하거나 부분 Replace 전에 삭제한다고 보장하지 않는다. 위 Binding 해제·Cluster 제거·실제 삭제 확인·재생성·새 Binding 순서를 별도로 Plan/리뷰한다. IAM/OIDC의 교체도 같은 원본/서비스 수명과 대조한다.

## 정적 검사와 실행 경계

Root에서 아래 정적 명령을 수행한다. Provider 다운로드와 State 초기화·Cloud Plan은 구분한다.

```bash
terraform version
terraform fmt -check
terraform init -backend=false -input=false
terraform validate
terraform providers schema -json > /protected/scratch/rosa-provider-schema.json
```

**이번 환경의 결과:** Core 1.16.4 공식 checksum·공개 Provider 설치/서명·Root Lock·fmt 확인. `validate`/schema는 Provider RPC의 Unix socket 생성이 `operation not permitted`로 차단되어 **미통과**다. 권한을 우회하지 않았으며 다른 Controller에서 같은 Source/Lock로 재검증해야 한다. 공식 v1.7.7 Source Schema 대조는 실행된 Provider Schema/validate PASS가 아니다.

예시는 의도적으로 실행 불가다. 실제 입력/Backend/Plan은 Git 밖 보호 영역, RHCS 인증은 `RHCS_TOKEN` 환경변수로 공급한다. 초기 input/support/예비 비용·Window 리뷰로 Owner의 첫 Plan을 준비하고, 생성 전 **실제 보호 전체 Plan의 최신 수량·누적/잔존·시간·Cost Gate와 팀 리뷰**를 확인한다. `execution_review` 문자열은 보조 검사이며 성공 증거/Apply 승인을 발급하지 않는다. `$450` 계획선 초과 신규 가동 보류·조정, 총 `$500` 한도를 유지한다. 이번 작업에는 Cloud 조회/Plan/Apply·유료 호출이 없다.

## 공식 자료와 남은 Gate

- [Red Hat Classic Terraform 절차](https://docs.redhat.com/en/documentation/red_hat_openshift_service_on_aws_classic_architecture/4/html/install_rosa_classic_clusters/creating-a-red-hat-openshift-service-on-aws-classic-architecture-cluster-with-terraform)
- [RHCS v1.7.7 Classic Schema](https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/docs/resources/cluster_rosa_classic.md)
- [RHCS v1.7.7 Default Worker pool](https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/docs/guides/worker-machine-pool.md)
- [RHCS v1.7.7 managed OIDC](https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/docs/resources/rosa_oidc_config.md)
- [공식 Classic v1.7.2 Operator Role/Trust/전파](https://github.com/terraform-redhat/terraform-rhcs-rosa-classic/blob/v1.7.2/modules/operator-roles/main.tf)
- [RHCS v1.7.7 실제 Delete 구현](https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/provider/clusterrosa/classic/cluster_rosa_classic_resource.go)

Controller 재검증·A/C/D Schema 수신·실제 Output/Route/Role/Policy·서비스 권한 PR·전체 Plan/Cost/Window·전파/부분 실패·Cluster/SG Binding·Node ECR Pull·TH-11 관리/Secret/GitOps·업무/Migration·T19 Recreate·T20/T23 정리가 남아 있다. 실제 CLI/Controller 버전·지원 조합도 실행 시 확인한다. 결과는 Issue/PR 원본, WORK_TRACKER/05에는 원본 링크·범위·남은 Gate를 연결한다.
