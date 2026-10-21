# Cloud activation preparation after ROSA recreation

> **B 전수 대조 후속 — 2026-10-21:** [최종 종료](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/B_ROSA_FINAL_CLOSEOUT_20261021.md)와 [실제 시험 결과](https://github.com/seokpan/seokpan-hybrid-docs/blob/main/execution/HPA_ATTEMPT04_VERIFIED_RESULTS_20261021.md)를 우선한다. B ROSA·정확한 소유 범위 잔존 자원·Snapshot4개 정리는 완료다. 아래 절차·입력 대기·명령은 작성 당시 기록 또는 재사용 계약이며 현재 재실행 요청이 아니다. A 자원과 다른 담당의 종료는 판정하지 않는다. [전체 대조](https://github.com/seokpan/seokpan-hybrid-docs/pull/140).

These tools prepare activation candidates and bind the existing read-only SQL probe to a new ROSA identity. They do not create ROSA, change IAM or Foundation, apply manifests, sync Argo, migrate a database or start workloads. Protected values and rendered runtime inputs stay outside Git.

## Activation previews

`prepare_cloud_candidate.py` verifies ten GitOps source blobs against `source_contract.json` (GitOps `c75a4b5e3b3b9bae4ea7d8a431962cb9ca7308d7`), copies them to a new directory and compiles three stages with Kustomize 5.7.1:

| Stage | Backend / Frontend | Purpose |
| --- | --- | --- |
| single | 1 / 1 | First actual provider, ingress and representative-business baseline |
| shared | 2 / 1 | Shared-state, reconnect and captured lifecycle convergence |
| target | 3 / 3 | Existing design target; PDB minAvailable 2 and soft zone/host spreading |

All rendered files are named `*.DO_NOT_APPLY.yaml`. Candidate values are one process per Backend, pool 2/overflow 0/timeout 5 seconds, and WS tunnel timeout 5 minutes. These values are preparation choices, not accepted runtime measurements or final performance limits. The target connection candidate is 7 peak Backend Pods x 1 process x 2 Engines x 2 connections = 28, plus 10 reserved, below the previously accepted DB limit 68. Account allowance 14 per service remains a candidate and must match final grants/limits.

The candidate explicitly selects `captured`; this does not authorize a transition. Old writers must be stopped, current Redis/DB state classified and the lifecycle rollout contract satisfied. Do not mix legacy and captured writers or delete keys to bypass this condition. Existing selected ECR digests and release-source annotations are retained; no rebuild is requested solely because a newer App main exists.

The source Release record is still `candidate / INCOMPLETE`. Rendering is not final release acceptance. Before any GitOps activation, fill current connection inputs, bind new identity and final Data evidence, validate the release composition, review its diff and perform the manual sync in the agreed write-control window. The generic `activation-target` path and its unresolved placeholders are not directly deployable.

```bash
python3 -I tools/cloud-readiness/prepare_cloud_candidate.py \
  --source /path/to/pinned-hybrid-gitops \
  --out /private/new-cloud-preview \
  --inputs /private/cloud-connection-inputs.json \
  --kustomize /path/to/kustomize
```

The input object allows only `route_host`, `database_host`, `redis_host`, and optional `ws_timeout`; no credentials. Omit the input file for a placeholder preview. New Route Host and Origin are derived from the new Cloud ingress and must be identical across FE/API/WS. Recovery Host selection is a separate decision and does not block this Cloud preparation.

## Post-import SQL on the new cluster

The historical Controller entry was bound to the deleted API, old Data Pod and delegated token UID. It must not be used after recreation. `post_import_new_cluster.py` instead binds the current lifecycle/ready-platform handoff, Namespace UID, new idle Data Pod UID/image, protected object UIDs and held-render hash. It reuses the accepted SQL probe bytes and two input-contract functions; it never loads an old delegated token or creates a Pod.

Fill `new-sql-target.TEMPLATE.NOT_ACCEPTED.json` from actual new-platform/temporary-operator outputs. Copy and fill the final Data handoff template only after final Import/compare/Schema and writer hold are accepted. Keep all real inputs mode 600 in a protected directory. A restored admin kubeconfig is not a personal tjung03 login; the execution entry requires the new personal context.

```bash
# Default: local identity/handoff checks only. No API or SQL call.
python3 -I tools/cloud-readiness/post_import_new_cluster.py \
  --lifecycle-config /private/rosa-lifecycle.json \
  --target /private/new-sql-target.json \
  --handoff /private/final-data-handoff.json \
  --receipt /private/new-unused-local-receipt.json

# After the actual final Data handoff, add --execute-read-only.
# Use a new receipt path; no output overwrite or automatic retry.
```

The probe checks purpose-account login, TLS, effective account/limit and zero-row table reads only. It does not prove full write grants, Pool lifetime, Migration current, business correctness, app activation or final Import correctness independently. Those remain separate actual results.

## Validation and remaining runtime work

Fourteen new synthetic boundaries cover missing handoff before APIs, old-cluster/old-Pod rejection, changed render/Namespace/CA binding, probe integrity, the new-target read-only flow, stage composition and credential-like host rejection. Kustomize locally compiles the three new stage candidates. These are new preparation checks; old installation, Pull/TLS/login, UI, smoke, CNI, Snapshot/decrypt and destruction results are reused.

Actual Controller transfer, current private inputs, new idle Data Pod, post-import SQL, server render, manual Sync, Cloud workload startup/HTTPS/WSS/business and resource/Pool acceptance are not executed by preparing this package. Do not infer readiness from a rendered candidate or from an owner name.

The [Cloud UI measurement adapter](../cloud-measurement/README.md) connects normal HTTP and UI vote-action-start-to-peer-WSS/UI latency correlated to the same committed game version. The time origin is the UI action start, not a server commit timestamp. It reuses the existing instrumentation and reports first-attempt errors and successful vote-intent throughput. Small 6-client and optional 12-client runs are presentation observations, not a replacement for the formal 10/30/60-client profiles, sustained load, full fault cases or RTO/RPO. Record actual sample counts, durations, Release, Replica, Pool, generator/resource limits and exclusions. Actual browser/Cloud execution remains untested until the explicit first run.
