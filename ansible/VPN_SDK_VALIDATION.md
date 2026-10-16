# VPN Ansible SDK 로컬 검증

- 작성자: 이유빈
- 작성일: 2026-10-07
- 관련 작업: h-infra #16
- 검증 결과 댓글: https://github.com/seokpan/seokpan-hybrid-infra/issues/16#issuecomment-6035357760

## 채택 조합

- 기존 Ansible Core: 2.14.18
- 기존 Python: 3.9.25
- AWS CLI: 2.37.5
- amazon.aws: 7.6.1
- community.aws: 7.2.0
- 접속 플러그인: community.aws.aws_ssm
- boto3: 1.40.76
- botocore: 1.40.76

기존 Ansible을 재설치하지 않고, 시스템 패키지를 읽을 수 있는
별도 가상환경에 AWS SDK를 설치해 검사했습니다.
추가 설치한 패키지 버전은 requirements-vpn-sdk.txt에 기록합니다.

검증 당시 시스템에서 사용한 SDK 의존성:
python-dateutil 2.9.0.post0, urllib3 1.26.5, six 1.15.0.
위 목록은 SDK 관련 시스템 의존성 기록이며 전체 환경 잠금 목록은 아닙니다.

## 검사 결과

| 검사 | 결과 |
|---|---|
| SDK 설치 | 통과 |
| Python 의존성 충돌 검사 | 통과 |
| 기존 시스템 Ansible 사용 | 확인 |
| SSM·S3 클라이언트 생성 | 통과 |
| SSM 접속 플러그인 로딩 | 통과 |
| 실제 SSM 접속·S3 파일 전달 | 미실행 |

클라이언트 검사에서는 실제 인증정보 대신 검증용 문자열을 사용하고
네트워크 연결을 차단했습니다. AWS API 호출 및 자원 변경은 없었습니다.

현재 실패가 발견되지 않아 B 후보 비교 시험은 진행하지 않습니다.
실제 AWS 접속·파일 전달·WireGuard 동작의 검증 완료를 의미하지 않습니다.

## 임시 환경 및 후속 작업

필요한 버전 목록과 검사 요약을 보존한 뒤 임시 검증 환경을 정리합니다.
임시 가상환경과 상세 출력은 저장소에 포함하지 않습니다.

다음 작업:
- 정식 사용자 전용 SDK 환경과 실행 방법 준비
- Session Manager 플러그인 버전 선택 및 설치 준비
- SSM 파일 전달용 S3와 운영 접속 주체의 권한 설계
- WireGuard 설치·설정용 Ansible 코드 준비
- 비밀키 공급·복구·인계 절차 작성
- 실제 AWS 시험은 비용·권한·실행 조건 확인 후 별도 진행

Terraform 실행 Role과 Ansible 운영 접속 주체의 권한을 구분합니다.
State 및 Data 백업 버킷을 파일 전달용으로 임의 재사용하지 않습니다.

## Controller Session Manager 플러그인 설치 기록

- 기록일: 2026-10-07
- 설치 대상: Controller, CentOS Stream 9 x86_64
- 채택 버전: 1.2.835.0
- 설치된 RPM: session-manager-plugin-1.2.835.0-1.x86_64
- 설치 방식: Ansible의 ansible.builtin.dnf로 검증한 로컬 RPM 설치
- 다른 패키지 자동 설치·업데이트 방지: 설치 시 패키지 저장소 비활성화
- AWS 공식 공개키 지문 대조 및 분리 서명 검증: 통과
- 설치 후 프로그램 실행 및 버전 확인: 통과
- 패키지 SHA-256: 1dcade7435709cce65a05eba87f2a96e8aaf7318c78a21d7a2ae648ce2f2ad81

공식 공개키 지문:
7959 6371 24CE 093A D501 D47A 2C4D 4AFF 6F67 57EE

실행 파일:
`/usr/local/sessionmanagerplugin/bin/session-manager-plugin`

RPM 설치 결과와 로컬 프로그램 버전을 확인했으며,
실제 AWS 접속·S3 파일 전달·Terraform Apply는 수행하지 않았습니다.
검증 기록 저장 후 다운로드 파일과 임시 GPG 키링을 정리합니다.
설치된 플러그인과 기존 공용 Ansible·Python은 유지합니다.

근거:
- https://docs.aws.amazon.com/systems-manager/latest/userguide/plugin-version-history.html
- https://docs.aws.amazon.com/systems-manager/latest/userguide/install-plugin-linux-verify-signature.html
