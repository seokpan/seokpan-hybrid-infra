# 기동 전 입력·실행 경로 연결

기존 Cloud 배포·정상/롤링 계측(PR89)과 HPA·원본 중지(PR90)를 사용하는 운영 보조 계층이다. 기존 승인 범위를 바꾸지 않으며, 이 디렉터리는 별도 검토 대상이다. 설정을 연결했다고 실제 생성·SQL·업무·측정이 성공한 것으로 판정하지 않는다.

## 파일과 책임

| 파일 | 처리 |
|---|---|
| finish_prestart.py | 기존 원본 탐색, 보호 입력 추출 재사용, 입력 변환 및 미충족 목록 |
| read_sources.py | 실패 하위 영수증·설정 해시·별도 인계 폴더의 원본 후보만 읽기. 등록·해독·자동 채택 없음 |
| finish_known_inputs.py / restricted_delegate.py | 관측한 전용 키의 실패만 대조·보존하고 검토된 두 명령 제한기로 연결. 기존 참조 입력은 현재값과 구분해 등록 |
| observer_access_plan.py | 실제 매칭된 db_admin의 전역 PROCESS 근거가 없을 때 기존 Data 관리자 세션용 최소 권한 계획만 생성. SQL 실행 없음 |
| materialize_inputs.py | 수락한 보존본의 공급 객체와 현재 계정 암호문의 선택 추출·해시 캐시 |
| readonly_handoff.py / d_generator.py | 별도 운영자에게 코드·config·plan·상태 읽기 ACL, 발생 결과의 제한 반환 |
| metadata_transport.py | 기존 강제 명령 키에 정해진 상태/외부 결과 명령만 추가 |
| windows_handoff.py | 설정으로 지정한 기존 발생기 경로에서 상태 조회·외부 확인·정상/롤링 결과를 수신 |
| pod_db_observer.py | 승인 Ready Backend의 기존 asyncmy와 CA로 읽기 전용 DB 관측 |
| db_bridge.py | 기존 MariaDB 호스트의 선택적 대체 경로. 기본 경로의 필수 조건이 아님 |
| bind_runtime.py | 새 플랫폼·정상 업무 근거를 기존 binder에 결합, Pool/계정·용량 상한 연결 |
| stages.sh | 기존 단계 실행·관측·외부 확인·DB 관측·HPA 중단/대조/복원 진입점 |

## 설치와 설정

Python 3.9 이상, Bash, 기존 oc와 PyYAML, SOPS/age 도구, 기존 PR89/90 소스 및 개인 보호 설정이 필요하다. Linux ACL 기능은 setfacl과 실제 사용자 계정을 요구한다. 자동 설치나 새 권한 확대는 수행하지 않는다.

저장소를 clone한 뒤 이 디렉터리를 **기존 작업 Run의 prestart-closeout 디렉터리**에 배치한다. stages.sh는 그 부모의 기존 session-paths.sh와 inputs/session-closeout.sh를 읽는다. 경로를 맞추지 않은 checkout에서 stages.sh를 실행하지 않는다. 이미 설치했다면 전체 준비를 반복하지 않고 실패한 하위 도구만 진단한다.

settings.TEMPLATE.NOT_ACCEPTED.json은 필수 키와 값의 형식을 보여주는 예시다. 환경의 실제 경로·개정·정확한 파일 해시로 채운 settings.json과 handoff-settings.json, transport-settings.json, input-registry.json은 Git에 넣지 않는다. 기존 운영 설정을 재사용하고, 템플릿을 실제 수락된 설정 위에 복사하지 않는다. 예시 자체는 실행 가능한 입력이 아니다.

```bash
# 실행 위치: 기존 Controller 작업 Run의 prestart-closeout
# 최초 준비에만 사용. 생성·SQL·Sync·부하는 실행하지 않음.
python3 -I finish_prestart.py --settings /absolute/private/settings.json
```

최초 준비는 필요한 로컬 해독, 기존 DB 호스트 메타데이터 SSH 조회, named ACL 및 해당 전용 키 강제 명령 등록을 수행할 수 있다. Cloud API를 호출하지 않는다고 로컬 파일 변경도 없는 것은 아니다. 성공한 추출·ACL·등록을 확인할 목적으로 전체 명령을 재실행하지 않는다. 추출 캐시는 입력 해시가 같은 경우에만 재사용한다.

Windows의 상태/외부 확인 경로도 windows-settings.TEMPLATE.NOT_ACCEPTED.json을 실제 기존 경로로 채운 보호 설정을 사용한다. `python windows_handoff.py report --settings <실제 설정>`은 상태만 조회한다. `external --stage <단계> --execute`, `baseline --execute`, `rolling --execute`도 같은 --settings가 필요하며 자동 실행하지 않는다. Node/Playwright 설치는 기존 것을 사용한다.

## 실행 경계와 실패 재개

이미 성공한 준비를 반복하지 않고 P05~P09 원본 위치만 읽으려면 기존 settings.json으로 `read_sources.py --settings <실제 설정> --search-root <기존 인계 폴더>`를 사용한다. 검색은 파일 수·깊이·크기를 제한하고 후보의 경로/해시만 반환한다. 값·자격·State·암호문을 내보내지 않으며 수정시각만으로 현재값을 수락하지 않는다. 다른 호스트의 원본은 이 로컬 조회로 없다고 판단하지 않는다.

- 입력이 없거나 서로 다른 원본이 둘 이상이면 PENDING으로 남긴다. 과거 한도·기본 Replica 10·과거 비용을 현재값으로 자동 수락하지 않는다.
- 새 노드 용량을 포함한 HPA 상한은 생성 전 입력으로 요구하지 않는다. 현재 DB/계정·노드 증거는 생성 후 실제 HPA 결합/활성 시 강제로 요구하며, Foundation·네트워크·현재 예산의 생성 전 조건은 유지한다. 앱 기동 전 목적 SQL/계정/Schema 확인도 그대로 유지한다.
- 원본 계약·조회·추가 Writer 계획은 BEFORE_SELECTED_MIGRATION으로 분류한다. 이전 설정의 BEFORE_CREATE도 같은 단계로 분류하되 미충족 항목은 이관 전 목록에 남긴다. 이관 실행의 중지·GO 검증은 생략하지 않는다. 다른 호스트의 원본을 Controller에서 못 찾았다는 이유로 복사나 새 보고서를 요구하지 않는다.
- 비용 근거는 --cost-reference로 지정한 소유자 전용 보호 파일에서 읽는다. 실제 조직 비용·청구액·자원 구성은 게시하지 않는다. 코드와 빈 템플릿만 제공하며 시작 시각·예산 수락은 바꾸지 않는다. 새 사용액·규격·유지 기간이 다르면 실제 실행 전에 다시 계산한다.
- DB 자격은 공유 ACL/메타데이터 전송 대상이 아니다. 기본 관측 경로는 기존 Backend에 private stdin으로 자격을 공급하며 Pod UID가 바뀐 표본은 실패다.
- DB별 권한 목록은 전역 PROCESS 권한을 증명하지 않는다. 계정별 전체 연결수 관측 전에 기존 관리자 실행 경로 또는 현재 권한 근거를 연결한다. 실제 CURRENT_USER 결과와 같은 db_admin 계정/Host에만 계획하며 계정이 없거나 다르면 중단한다. 이미 있거나 미확인이면 GRANT 계획을 만들지 않는다. 애플리케이션 계정·비밀번호·GRANT OPTION은 변경하지 않는다.
- 메타데이터 전송은 임의 경로 읽기·일반 셸·포트 전달을 제공하지 않는다. 원래 rolling/guard 명령은 기존 제한기에 위임한다. 기존 제한기 해시는 최대 두 개의 명시된 값만 허용하며 공백을 일반적으로 제거해 허용하지 않는다.
- 설치가 중간에 멈추면 original-dispatch.json과 현재 강제 명령을 대조한다. 파일을 삭제하거나 등록을 반복해 표식을 피하지 않는다.
- 관측한 기존 코드의 해시가 소비 도구와 다르더라도 그 해시를 추가해 알 수 없는 원문을 실행하지 않는다. 명시한 원본/기존 모듈 해시와 현재 강제 명령을 대조한 뒤, 원본 파일을 보존하고 검토된 restricted_delegate.py로 기존 probe·두 정확한 명령만 연결한다. 다른 키·셸 접근·ACL은 바꾸지 않는다. STARTED 표식 뒤 실패하면 자동 반복하지 않는다. 기본 prepare나 완료한 probe·해독은 호출하지 않는다.
- PENDING 영수증의 reason과 남은 키를 먼저 수집한다. 보호 파일 본문·authorized_keys·토큰·원문 로그는 공유하지 않는다.
- SSH 로그에 Load key ... Permission denied가 있으면 먼저 로컬 실행 계정의 키 읽기 제한을 구분한다. 그 뒤의 서버 인증 거부만 보고 키 재등록·sshd 변경을 하지 않는다. 기존 키를 읽을 수 있는 허용된 실행 환경을 사용하며 키 ACL을 넓히지 않는다.
- 새 실제 Run이 시작된 뒤에는 전체 prepare 대신 stages.sh의 해당 단계와 기존 work를 사용한다. trigger 결과가 불명이면 재실행보다 reconcile을 먼저 한다.

생성 이후 순서와 관측·복원 한계는 [실행 계약](../../docs/PRESTART_CLOSEOUT_RUN.md)에 정리했다. 기본 실행에 ROSA 생성·삭제는 없다.

## 검증

test_closeout.py는 새 보조 계층의 경계만 검사한다. Windows 파일/인자 모의 검사는 실제 Linux ACL, RDS, SSH, 브라우저, SMTP 또는 새 클러스터의 성공을 증명하지 않는다. Linux CI도 OS 간 코드 경계 검사이며 실환경 검사가 아니다. 기존 프로젝트 전체 시험이나 완료한 런타임 검사는 이 변경의 검증으로 재실행하지 않는다.
