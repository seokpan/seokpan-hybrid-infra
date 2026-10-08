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

## 공식 정책 묶음 수신·무결성 확인 — 2026-10-08

- 자료 참조: `rosa-policy-20261008T061153Z-6caf4295`
- 기존 Controller의 예비 공급자 계정에서 수신 계정의 Git 밖 보호 영역으로 사본 전달.
- 원본 17개 파일과 manifest 등록 SHA-256 일치 확인.
- 원본·사본 18개 파일의 해시 일치 확인.
- 수신 사본 소유자 ansible, 폴더700·파일600 확인.
- 원본과 원본 권한은 유지하며 정책 재조회·토큰 전달은 수행하지 않음.
- manifest SHA-256: `a1702e4cbfb70aaa70a04419b828d45a6d92a6ced281be2a170be44847ade450`
- 구성: Classic 권한 정책4개·Support Trust1개·Operator 정책7개·OCM 참조4개·manifest·source-records.
- 미제공 Operator 정책의 실제 필요 여부는 현재 ROSA Operator 목록과 대조 후 판단.

해시 검사는 저장·전달 내용의 일치 확인이며 독립적인 원본 진위 증명이 아니다.
자료 수신을 실제 AWS 역할 생성·권한 적용·ROSA 준비 완료로 표현하지 않는다.

다음 검토:
- 역할별 공식 권한 정책의 적용 범위.
- Installer·Support·ControlPlane·Worker 신뢰 정책과 공식 근거.
- 실제 Operator policy_name과 제공 정책의 대응.
- 기존 AWS 자원·State 관리 주체 및 Terraform 실행 역할의 필요 권한.

이번 단계에서 AWS/Red Hat 접속·Terraform Plan·Apply는 미실행이다.

## 공통 IAM 생성 코드·모의시험 결과 — 2026-10-08

### 작성한 코드

- 공통 역할 4개: Installer·Support·ControlPlane·Worker.
- 역할별 권한 정책 4개와 Operator 정책 6개.
- 공통 역할과 역할별 권한 정책의 연결 4개.
- 역할 ARN 4개와 Operator 정책 ARN 6개의 제한 출력 선언.
- 공식 권한 내용과 전달받은 Operator 소비 이름 유지.
- 생성 기본값은 false로 유지.

### 실행자가 확인한 검사 결과

- Terraform 형식·구성 검사 통과.
- Terraform 1.16.4와 기존 AWS Provider 6.67.0을 재사용.
- 실제 Foundation Backend와 다른 자원을 제외한 임시 구성 사용.
- 활성·비활성 모의시험 2개 통과.
- 활성 설정의 역할 4개·정책 10개·연결 4개 확인.
- 역할·정책 이름, 공식 권한 내용, 신뢰 정책과 연결 확인.
- 제한 출력의 항목 수 확인.
- 비활성 설정에서 이번 IAM 자원과 출력 항목 없음 확인.
- 시험용 버전 0.0과 가짜 ARN은 실제 공급값이 아님.
- 임시 구성·정책 사본·모의시험 파일은 종료 시 정리.
- 프로젝트 코드·공식 정책 원본·기존 잠금 파일 유지.

### 검증 범위와 남은 작업

이번 검사는 AWS 모의 기능을 사용한 코드 구조 확인이다.
실제 AWS 권한 성공·지원 버전·설치 가능 여부를 증명하지 않는다.

실제 계정의 기존 IAM 자원과 State 관리 주체, 실행 권한,
Installer·Support 신뢰 대상의 사용 가능 여부, 추가 신뢰 조건,
권한 경계 필요 여부와 실제 지원·소비 버전은 적용 전에 대조한다.

최신 main 반영과 코드 리뷰, 전체 Foundation Plan,
비용·실행 승인, 실제 Apply 및 제한 출력의 보호 인계가 남아 있다.

이번 단계에서 AWS·Red Hat 접속, 실제 Foundation Plan·Apply,
실제 역할·정책 생성은 수행하지 않았다.

### 관련 기록

- #47 상세 결과:
  https://github.com/seokpan/seokpan-hybrid-infra/issues/47#issuecomment-6058853653
- #25 진행 결과 연결:
  https://github.com/seokpan/seokpan-hybrid-infra/issues/25#issuecomment-6058859011
