# ROSA 공통 IAM 구현·인계 준비

- 작성자: 이유빈
- 작성일: 2026-10-08
- 작성내용: 이슈 #47의 구현 경계와 입력 확인 기준
- 상태: 구현 준비. 실제 자원과 권한은 미확인

## 구현 경계

- Foundation: Installer·Support·ControlPlane·Worker 역할 4개와 공통 Operator 정책.
- Bootstrap: Foundation·ROSA Terraform 실행 역할에 필요한 관리 권한.
- ROSA: 클러스터·OIDC·Operator 역할과 정책 연결.
- Worker ECR Pull 정책과 연결은 기존 Registry 담당 코드와 대조하며 중복 선언하지 않는다.
- 기존 bootstrap/foundation/rosa Root와 State를 유지한다.

## 기존 환경 유지

- Terraform Core: 1.16.4
- AWS Provider: 6.67.0
- ROSA Root RHCS Provider: 1.7.7
- Foundation의 Provider 추가 여부와 정책 공급 방식은 별도로 검토한다.
- 공식 예제 Module을 참고했다는 이유로 Module이나 Provider를 자동 추가하지 않는다.

## Terraform 구현 전에 확인할 입력

- [ ] 기존 AWS 역할·정책의 존재 여부와 관리 주체
- [ ] 공통 역할 이름 접두사와 IAM 경로
- [ ] Installer·Support 역할의 공식 신뢰 대상
- [ ] ControlPlane·Worker 역할의 공식 신뢰 정책
- [ ] 역할별 공식 권한 정책 원본과 지원 ROSA 계열
- [ ] 실제 Operator 정책 이름과 공통 정책 ARN의 대응
- [ ] Permissions Boundary·External ID의 필요 여부
- [ ] Worker ECR Pull 정책의 연결 경계

현재 ROSA 코드는 Operator 역할 6개를 예상한다.
이는 실제 서비스 조회로 목록을 확인한 결과가 아니다.

## 정책 원본과 재현성

RHCS 1.7.7의 rhcs_policies는 Red Hat 서비스에서 정책을 조회한다.
Provider 버전 고정만으로 반환 정책 내용이 고정됐다고 판단하지 않는다.
채택한 정책 원본·조회 시점·해시와 정책 공급 방법을 기록하고 리뷰한다.
Support 신뢰 대상과 정책 JSON을 추측해서 채우지 않는다.

## 인계 기준

- account_role_prefix·iam_path와 역할 ARN 4개를 전달한다.
- operator_policy_arns는 실제 policy_name을 키로 사용한다.
- 생성 전 계산한 ARN을 실제 생성 결과로 기록하지 않는다.
- 실제 인계는 승인된 Apply 이후 필요한 Output만 보호된 경로로 제공한다.
- 전체 Output dump나 terraform_remote_state를 추가하지 않는다.

## 실행 경계

현재 단계에서는 AWS 접속·정책 조회·Terraform Plan·Apply를 실행하지 않는다.
실제 실행 전 전체 Plan·비용 검토·팀 리뷰·실행 승인을 거친다.

## 근거

- https://github.com/seokpan/seokpan-hybrid-infra/issues/47
- https://github.com/seokpan/seokpan-hybrid-infra/blob/main/terraform/rosa/README.md
- https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/docs/data-sources/policies.md
- https://github.com/terraform-redhat/terraform-provider-rhcs/blob/v1.7.7/provider/ocm_policies/classic/ocm_policies_data_source.go

## 채택한 공통 역할 이름

- 결정일: 2026-10-08
- 역할 접두사: `seokpan-fnd-rosa`
- IAM 경로: `/`
- 생성 기본값: `false`

| 용도 | 역할 이름 |
|---|---|
| 설치 | `seokpan-fnd-rosa-Installer-Role` |
| 지원 | `seokpan-fnd-rosa-Support-Role` |
| 제어 노드 | `seokpan-fnd-rosa-ControlPlane-Role` |
| 작업 노드 | `seokpan-fnd-rosa-Worker-Role` |

기존 Foundation의 seokpan-fnd-rosa 이름과 공식 역할 접미사를 따른다.
개인 이름·날짜·특정 클러스터 이름은 공통 역할 이름에 넣지 않는다.
클러스터 전용 Operator 역할 접두사는 ROSA 담당 범위에서 별도로 정한다.

이 기록은 이름 선택이며 실제 AWS 역할 존재나 생성 완료를 의미하지 않는다.
기존 자원 존재 여부와 관리 주체는 실제 생성 전에 확인한다.
