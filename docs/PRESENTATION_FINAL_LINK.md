# 현재 앱에서 최소 발표 시험으로 연결

## 확인된 실제 결과

- 앱 Backend 1 / Frontend 1 Ready, 현재 모니터링 Synced/Healthy 및 Backend up=1은 기존 성공 기록을 재사용한다.
- DB 전체 연결 수는 2026-10-20 19:46:46.773707 KST 표본에서 7, 전역 한도 68이다. 관측 연결을 포함하며 계정별 수, 비앱 연결 기준선, 연속 최댓값은 확인되지 않았다.
- 공유 루트는 `/var/tmp/seokpan-hpa-handoff`, 읽는 사용자는 `cyj`다. 기존 HPA 설정과 Plan은 미게시 상태다.
- 개인 계정의 machineautoscalers 조회는 Forbidden이다. 이 결과만으로 머신 자동확장 활성 여부를 판단하지 않는다.

## 이번 변경

1. 관리 컨텍스트로 tjung03에 openshift-machine-api의 machineautoscalers get/list/watch만 부여한다. 기존 동명 역할의 소유권이나 범위가 다르면 중단한다. 개인 계정으로 읽은 min/max가 다르면 앱 변경 전에 중단한다. 머신 설정 자체는 변경하지 않는다.
2. 기존 단일 Source와 UID가 맞을 때 Backend만 CPU request 100m / limit 500m, memory request 256Mi, Replica 2로 준비한다. Frontend 1과 이미지, DB Pool, Schema는 유지한다. 이 준비 교체는 발표 비교의 롤링으로 측정하지 않는다. 소유 Application get/patch가 부족한 경우 같은 Application 한 개에만 권한을 추가한다.
3. 실제 Ready 이후부터 30분의 시험 입력을 만든다. 6클라이언트, 2방, 준비 60초, 관측 180초의 정상/롤링을 같은 설정으로 비교한다. 이 30분은 시험 입력 유효 시간이며 비용 상한이나 전체 자원 자동 종료 시간이 아니다.
4. 기존 고정 경로에 롤링 Plan을 준비하고, 새 실행 식별자의 읽기 전용 공유 폴더에 정상/롤링 입력을 게시한다. HPA config/Plan은 게시하거나 수락하지 않는다. 이 공유 입력은 HPA guard 상태와 구분한다.
5. DB 전체 연결 수 기록기는 준비 Source와 단일 롤링 이후 Source 두 개만 수락할 수 있다. FULL/HPA 관측은 여전히 Source 하나와 전체 계정 가시성을 요구한다. 미확인 계정별 연결 수를 0으로 바꾸지 않는다.

## 실행과 재개

`finish_presentation_link.py --observer-source <고정 개정의 pod_db_observer.py> --execute`는 조회 권한과 비교용 설정, Plan을 준비한다. 실제 부하나 HPA는 실행하지 않는다. 성공 기록이 있으면 클러스터 작업 없이 그 기록을 재사용하고 시간 만료 여부를 표시한다.

성공 뒤 출력의 `observer_start_argv`를 실행하면 새 시험의 Pod/Node 변화와 DB 전체 연결 수를 최대 900초 기록한다. 각 시작 시도는 한 번만 허용하며 프로세스 시작을 표본 수집 성공으로 간주하지 않는다. 이후 기존 `run-observer.mjs`로 정상 측정 성공을 확인한 뒤 롤링 비교를 실행한다. 두 시험을 겹치지 않는다. B의 기존 제한 SSH 경로가 롤링 1회를 호출하고, D에게 개인 kubeconfig나 키를 전달하지 않는다.

준비 Sync 응답을 잃으면 재제출하지 않는다. 같은 명령 재개는 Source와 실제 Ready를 읽어 이어갈 수 있다. 롤링 trigger 응답이 불명확하면 기존 trigger 표식과 현재 Source를 대조하며 다시 trigger하지 않는다. 기존 pause/recover/verify 명령을 사용하고, 복귀도 별도 실행 기록으로 남긴다.

## 남은 실제 실행

- 이번 Controller 준비의 실제 권한/Ready/게시 결과는 아직 없다.
- 정상/롤링 요청·업무 분모, 성공·실패·불명·재시도, WSS 재연결·상태 수렴, 실제 Pod/Node·DB 표본은 생성 후 확보한다.
- 선택 HPA는 별도 60분, 전체 계정 PROCESS 관측, 3/3 및 최종 용량/config 조건이 남아 있다. 현재 전체 표본 7을 HPA 통과 근거로 사용하지 않는다.
- 시연 녹화·결과 보존·사용자 승인 후 삭제는 시험 결과와 구분한다. NAT/EIP·Data·Snapshot은 이번 코드가 변경하지 않는다.

새 오프라인 경계 검증 7건만 수행한다. 기존 완료 런타임·SQL·설치·앱/모니터링 Sync는 반복하지 않는다.
