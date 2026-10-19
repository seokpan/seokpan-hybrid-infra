# 기동 전 입력·실행 경로 연결

기존 Cloud 배포·정상/롤링 계측(PR89)과 HPA·원본 중지(PR90)를 사용하는 운영 보조 계층이다. 기존 승인 범위를 바꾸지 않으며, 이 디렉터리는 별도 검토 대상이다. 설정을 연결했다고 실제 생성·SQL·업무·측정이 성공한 것으로 판정하지 않는다.

## 파일과 책임

| 파일 | 처리 |
|---|---|
| finish_prestart.py | 기존 원본 탐색, 보호 입력 추출 재사용, 입력 변환 및 미충족 목록 |
| materialize_inputs.py | 수락한 보존본의 공급 객체와 현재 계정 암호문의 선택 추출·해시 캐시 |
| readonly_handoff.py / d_generator.py | 별도 운영자에게 코드·config·plan·상태 읽기 ACL, 발생 결과의 제한 반환 |
| metadata_transport.py | 기존 강제 명령 키에 정해진 상태/외부 결과 명령만 추가 |
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

## 실행 경계와 실패 재개

- 입력이 없거나 서로 다른 원본이 둘 이상이면 PENDING으로 남긴다. 과거 한도·기본 Replica 10·과거 비용을 현재값으로 자동 수락하지 않는다.
- DB 자격은 공유 ACL/메타데이터 전송 대상이 아니다. 기본 관측 경로는 기존 Backend에 private stdin으로 자격을 공급하며 Pod UID가 바뀐 표본은 실패다.
- 메타데이터 전송은 임의 경로 읽기·일반 셸·포트 전달을 제공하지 않는다. 원래 rolling/guard 명령은 기존 제한기에 위임한다. 기존 제한기 해시는 최대 두 개의 명시된 값만 허용하며 공백을 일반적으로 제거해 허용하지 않는다.
- 설치가 중간에 멈추면 original-dispatch.json과 현재 강제 명령을 대조한다. 파일을 삭제하거나 등록을 반복해 표식을 피하지 않는다.
- PENDING 영수증의 reason과 남은 키를 먼저 수집한다. 보호 파일 본문·authorized_keys·토큰·원문 로그는 공유하지 않는다.
- 새 실제 Run이 시작된 뒤에는 전체 prepare 대신 stages.sh의 해당 단계와 기존 work를 사용한다. trigger 결과가 불명이면 재실행보다 reconcile을 먼저 한다.

생성 이후 순서와 관측·복원 한계는 [실행 계약](../../docs/PRESTART_CLOSEOUT_RUN.md)에 정리했다. 기본 실행에 ROSA 생성·삭제는 없다.

## 검증

test_closeout.py는 새 보조 계층의 경계만 검사한다. Windows 파일/인자 모의 검사는 실제 Linux ACL, RDS, SSH, 브라우저, SMTP 또는 새 클러스터의 성공을 증명하지 않는다. Linux CI도 OS 간 코드 경계 검사이며 실환경 검사가 아니다. 기존 프로젝트 전체 시험이나 완료한 런타임 검사는 이 변경의 검증으로 재실행하지 않는다.
