# 기존 결과로 HPA 실행 입력 결합

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/pull/140).

기존 Controller HPA 입력이 빈 상태여서 발생기에 실행 파일을 전달하지 못한 문제를 보완한다. 기존 실제 3/3·목적 계정·이미지·Pool 결과를 결합하고 새 시험에 필요한 노드 요청 용량만 계산한다. 기존 설치·Schema·앱 Sync·정상 시험은 재실행하지 않는다.

`finalize_hpa.py execute --activate`는 새 run_id의 config·선언 plan·미활성 상태를 먼저 게시한다. 이어 현재 RDS 마스터 자격을 메모리로 받아 실제 매칭된 db_admin 계정에 PROCESS만 부여한다. 기존 PROCESS는 재부여하지 않으며 시도 표식 이후 결과가 불명확하면 권한 쓰기를 자동 반복하지 않는다. 목적 계정·Schema·비밀번호는 변경하지 않는다.

FULL DB 관측이 실제 통과해야 기존 control.py가 실제 준비 plan을 만든다. 먼저 게시한 선언 plan과 같은지 확인한 뒤 임시 HPA를 생성하고 기존 관측기와 읽기 전용 상태 게시기를 연결한다. 미활성 상태를 실행 GO로 표시하지 않는다. D 부하는 이 코드에서 실행하지 않으며 최종 성공 JSON에 D의 실행·중단·반환 명령을 제공한다.

75분의 입력 유효 구간 안에서 활성화 이후 최소60분이 남는지 확인한다. 관측기는 기존45분, 상태 게시기는 최대60분으로 제한한다. 상태가45초보다 오래되거나 제어 중지 시 발생기는 중단해야 한다. 새벽03:00 KST 이전 ROSA 삭제 완료 조건보다 늦은 시험 입력을 거부한다. 실제 ROSA 삭제는 수행하지 않는다.

정상 시험은 실제 완료됐고 롤링 시나리오는 준비 중 중단돼 실제 롤링은 호출되지 않았다. 해당 결과를 근거로 참조하며 HPA를 롤링 성공의 대체 근거로 사용하지 않는다. 마스터 Secret 접근 권한이나 회전 상태가 막히면 그 실제 오류만 남긴다. 자격값은 로그·argv·게시 파일에 저장하지 않는다.

이번 PR은 병합된 #89/#90/#91/#92/#95~#105의 승인과 별도 변경이다. 로컬 경계 검증과 GitHub 게시가 실제 PROCESS 부여·HPA 생성·D 부하 성공을 뜻하지 않는다. 새 시험의 종료·원복과 증거 수집은 최종 JSON의 명령으로 수행한다.
