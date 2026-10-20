# HPA 시험 종료와 B 담당 자원 정리

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/blob/078b93184790231fbc3b0e03c9ce0b767ab39dab/execution/TJUNG03_FULL_REPOSITORY_AUDIT_20261021.md).

## 2026-10-21 최종 종료 결과

사용자 제공 Controller 결과로 B ROSA 삭제 Apply와 State managed instance0, 원격 클러스터 부재, 정확한 현행 owned 태그의 EC2/EBS/ENI/LB 각각0을 확인했다. B Snapshot4개도 삭제 후 개별 조회에서 모두 ABSENT였다. 확인 범위 내 B 자원 정리는 완료이며 A 자원은 변경하지 않았다. 완료한 설치·부하·조회·삭제를 다시 실행하지 않는다.

계정 전체 과금0·태그 없는 자원/공유 자원 부재·다른 담당 자원의 정리는 이 결과로 확정하지 않는다. 최종 비용 청구액은 조회하지 않았다. 실제 결과와 한계는 h-docs의 `execution/B_ROSA_FINAL_CLOSEOUT_20261021.md`에 후속으로 기록한다.

## 저장소 설치·체크섬 해석

저장소의 `post_test_metadata_transport.py`를 실행 환경의 `prestart-closeout/metadata_transport.py`로 설치한다. 저장소에 없는 `tools/cloud-prestart/metadata_transport.py`를 입력으로 요구하지 않는다. 설치기는 모든 소스와 기존 대상의 허용 해시를 먼저 확인한 뒤 파일을 반영한다. 누락 입력이나 미승인 로컬 변경이 있으면 대상 파일을 바꾸기 전에 중단한다.

`post-test-SHA256SUMS`와 `final-teardown-SHA256SUMS`는 현재 저장소의 실제 파일명·바이트를 검증하는 목록이다. 이전 배포 해시는 Git 이력과 설치기의 명시적 허용 목록으로 보존한다. 최신 전체 종료 목록에는 독립 B 수집기와 외부 D 인수 도구도 포함한다.

GitHub 보존 참조는 자동 게시 증명이 아니다. 참조를 기록하기 전에 게시 URL을 직접 열거나 저장소 API로 실제 파일 존재를 확인하고, 내려받은 ZIP의 SHA256이 Controller의 공개 ZIP 해시와 같은지 대조한다. URL 모양만 확인한 상태에서 게시 완료로 기록하지 않는다.

아래 절은 당시 실행 준비와 전환 이력이며 최종 완료 판정과 구분한다.

## 당시 확인한 상태

- B 관측·게시 GO 성공. 같은 HPA 유지, HPA 재생성 없음, cloud_resource_writes=0.
- 활성 config SHA256: `cb82bcf65e8f9338af8a24e6b549d481c622035dfc69b741aedae537f13493b8`.
- D 전달 기준 시험 시작: 2026-10-20 23:28 KST. 분 단위 통보이며 종료 영수증과 구분한다.
- 관측 상한: 2026-10-21 00:30 KST. D가 먼저 완료하면 즉시 종료 절차를 진행한다.
- 실제 HTTP·확장/축소·DB 최댓값·원복·AWS 삭제 결과는 아직 미확인이다.
- 정상 업무와 롤링 시험, 별도 HPA의 판정과 분모를 합치지 않는다.

## 실행 순서

1. `install_post_test_tools.py`: 종료 도구와 고정된 공개 증거 전달 경로만 연결한다. 프로세스·HPA·DB·앱을 변경하지 않는다.
2. D가 완료 또는 중단 결과를 반환한 뒤 `finish_hpa_time_copy.py --finish-run`을 실행한다. 반환 manifest와 같은 Run의 종료 영수증을 대조하고 B 관측 중지, 해당 HPA 원복, Ready 확인, B/D 결과 결합을 수행한다.
3. 공개 ZIP과 비공개 원본 보존물을 따로 만든다. `PASS_RUN_STOPPED_RESTORED_EVIDENCE_PRESERVED_NOT_TEST_VERDICT`는 시험 성공 판정이 아니다. `test_verdict`와 D 종료 상태를 별도로 기록한다.
4. 기존 제한된 SSH 경로의 `b-hpa-evidence-summary-v1`과 `b-hpa-evidence-archive-v1`으로 공개 사본만 가져와 GitHub에 보존한다. `b-hpa-publication-proof-v1`에 실제 게시 URL과 같은 ZIP의 SHA256을 전달해 보존 참조를 남긴다. 새 SSH 키 등록이나 셸 권한은 부여하지 않는다.
5. `bash run_b_final_delete_plan.sh --prepare-plan`: 현재 ROSA 루트와 새 클러스터 ID에 대한 새 삭제 Plan만 준비한다. 과거 삭제 Plan을 사용하지 않는다.
6. Plan을 검토한 뒤 `bash run_b_final_delete_plan.sh --apply-reviewed-plan`: 같은 Plan·State·Source·GitHub 보존 참조를 대조하고 사용자 확인 문구를 입력해야 실제 삭제한다. 실패 또는 부분 적용 표식이 있으면 자동 재시도하지 않는다.
7. `bash run_b_final_delete_plan.sh --inventory`: ROSA 원격 목록에서 해당 ID 부재와 현재 infra 소유 EC2·EBS·ENI·LB 및 B Snapshot 네 개만 조회한다. 조회 실패는 0개가 아닌 미확인으로 남긴다.
8. 실제 Snapshot 대조 뒤 `bash run_b_final_delete_plan.sh --delete-reviewed-snapshots`: 별도 확인 문구로 정확한 B Snapshot만 삭제한다. 이후 새 조회로 부재를 확인하고 GitHub에 최종 결과를 추가한다. 남은 ROSA 소유 자원이 있으면 실제 ID와 소유 근거로 후속 조치를 정한다.

## 삭제 범위

현재 ROSA 루트의 클러스터·Operator 역할/연결·OIDC·Worker 바인딩과 B가 보존했던 monitoring Snapshot 네 개가 B 범위다. A Foundation·Data·VPN·NAT·EIP·공통 S3·Bootstrap과 다른 담당 ECR은 제외한다. B 정리 결과로 계정 전체 과금 0을 주장하지 않는다.

## 증거의 한계

실제 표본 시각·수집 간격·최대 누락 간격을 남긴다. B 전체 관측 구간과 실제 부하 구간을 구분하고 설정상 상한을 관측 최댓값으로 쓰지 않는다. HTTP Guest 로비/랭킹 읽기 결과는 게임/WSS 처리량이나 최대 수용량이 아니다. 첫 중단 실행의 Guest 정리 여부는 미확인으로 유지한다.

## 검증 범위

새 관측기 재사용 분기 3건 확인. 종료 도구의 공개 필드 제외·완료 작업 재사용·불확실한 쓰기 재시도 차단 4건 통과. Windows symlink 권한 시험 1건은 환경상 skip. Python 3.9 및 새 shell 문법과 언어 검사 통과. 완료한 런타임 검사는 반복하지 않았다. 새 종료·삭제 도구의 Controller 실행은 아직 미확인이다.

## 2026-10-21 최종 폐기 경로로 변경

사용자가 기록 인수 후 프로젝트를 완전히 종료하는 경로를 선택했다. 새 명령은 `finish_hpa_final_teardown.py --finish-run --final-teardown-no-restore`이며 D 종료 영수증과 해시를 대조하고 B 관측기·게시기를 종료한 뒤 기록을 보존한다. 이전 배포 설정 복원과 정상화 검사는 삭제 선행 조건에서 제외한다. `restoration_verified=false`와 `SKIPPED_FINAL_PROJECT_TEARDOWN`을 명시하고 복원 성공이나 HPA 전체 시험 성공으로 표시하지 않는다. 기존 복원 경로 코드는 기록으로 유지한다.

D 전달 종료 결과: ABORTED_HPA_LOAD / RECENT_ERROR_BUDGET_EXCEEDED, 진행 요청 0. 2026-10-20 23:28:20~23:43:28 KST, 요청 47,138건. VU0/10/50/100 단계 계획 시간 완료, VU200 단계 2.2초 후 중단. 게스트 300 생성·300 정리, 미확인 0. 파일 직접 대조와 HTTP 오류 분류는 아직이다. GENERATOR_EXIT=0은 결과 전달 도구 종료이며 부하 시험 성공을 뜻하지 않는다. 재실행하지 않는다.

기록 보존과 새 B 삭제 Plan 준비는 일괄 진행한다. 실제 삭제는 GitHub 보존 참조·새 Plan 범위를 확인하고 사용자 승인을 받은 뒤에만 수행한다. A 담당 자원 제외는 유지한다.

