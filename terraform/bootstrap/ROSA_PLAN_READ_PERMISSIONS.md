# ROSA 최초 Plan의 기반 조회 권한

관련: Infra #25. 실제 실행 조건은 ROSA Root의
[단계별 호출 수요표](../rosa/REVIEW_AND_EXECUTION_GATES.md)를 따른다.

## 범위

기존 Backend 정책과 분리한 선택적 개별 정책이다.
기본값은 비활성이며, 최초 빈 State Plan의 기반 조회를 대상으로 한다.

| 작업 | 대상·조건 |
|---|---|
| VPC·Subnet·Route Table 목록 조회 | 서울 리전, 목록 API의 Resource는 `*` |
| VPC 속성 조회 | 보호 입력으로 공급한 실제 Foundation VPC ARN 하나 |
| 공통 역할 조회 | 승인한 Installer·Support·ControlPlane·Worker 역할 4개 |
| 정책 연결 대상 | `seokpan-tf-rosa` 실행 역할 하나 |

목록 조회는 특정 VPC 하나로 IAM 자원 범위를 제한하는 방식이 아니다.
실제 소비 코드는 인계받은 VPC·Subnet·역할 참조를 대조한다.

IAM/OIDC 생성·변경·삭제, 기존 State의 종속 자원 Refresh,
정책 본문 조회, Data SG 변경 및 PassRole은 이번 범위가 아니다.
ROSA 전체 서비스 권한 준비 완료로 표시하지 않는다.

## 입력

- `enable_rosa_plan_read_permissions`: 기본 `false`.
- `rosa_plan_foundation_vpc_id`: 기본 `null`.
- 활성화하려면 Foundation 적용 후 확인한 실제 VPC ID가 필요하다.
- VPC ID는 Git 밖 보호 입력으로 공급하며 추측한 ID를 사용하지 않는다.

## 실행자가 확인한 검사 결과

- Terraform 형식·구성 검사 통과.
- `scripts/test-rosa-first-plan-read.py`의 격리 모의시험 4개 통과.
- 기본 비활성 상태에서 새 정책 없음.
- 활성 시 ROSA 역할에 정책 하나와 정확한 조회 범위 연결.
- 기존 정책·연결 유지 및 역할별 개별 정책 합계 크기 제한 확인.
- VPC 입력 누락과 잘못된 형식 차단.
- 시험용 계정·VPC와 기존 Provider를 사용.
- 실제 Backend·AWS API·실제 Plan·Apply는 미실행.

모의시험은 실제 목적 역할의 유효 권한이나 서비스 성공을 증명하지 않는다.

## 실제 적용과 후속

1. 코드·문서·검사 결과 리뷰 및 병합.
2. 실제 Foundation VPC ID와 승인된 계정·입력 확보.
3. 기존 bootstrap 보호 입력을 유지한 전체 Plan과 변경 검토.
4. 해당 실행 승인 후 적용·재검사.
5. B 본인 목적 역할로 Backend와 필요한 조회 호출 검증.
6. 생성·변경·정리 권한은 실제 단계별 수요와 대상에 맞춰 별도 보완.

기존 `enable_rosa_account_iam_permissions` 보호 입력을 누락하지 않는다.
ROSA 조회 권한 활성화가 기존 Foundation 관리 권한을 대체하지 않는다.

Foundation 전체 Plan은 별도로 진행할 수 있다.
이 조회 권한의 적용 완료를 Foundation Plan의 일괄 선행조건으로 추가하지 않는다.

## PR #58 기존 검사 회귀 보완

- 기존 bootstrap 검사의 예상 정책 선언에 선택적 조회 정책을 포함했다.
- 공용 시험 구성과 역할별 개별 정책 합계 계산에 조회 정책을 반영했다.
- 조회 정책의 활성·비활성 및 합계 반영 검사를 추가했다.
- 새 조회 검사에서 정책의 중복 삽입을 제거했다.
- 병합된 기준 코드와의 비교에서도 기존 IAM 선언 검사를 유지했다.
- Controller에서 동일한 수정 코드로 기존 모의시험 2개와 신규 모의시험 4개가 모두 통과했다.
- 기존 권한·연결, 대상 제한, 입력 차단 및 정책 크기 검사를 유지했다.
- 실제 권한 자원 선언은 변경하지 않았다.

시험에서 추가 관리형 정책은 3,807자였다.
활성 상태의 역할별 개별 정책 합계는 bootstrap 2,310자,
foundation 7,444자, rosa 1,376자로 검사 제한 안에 있었다.

이 결과는 모의시험이며 실제 AWS 권한 성공을 증명하지 않는다.
실제 Backend·AWS API·Plan·Apply는 이번 검사에서 실행하지 않았다.
