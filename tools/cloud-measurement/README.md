# Cloud normal-business measurement

This package connects the existing HTTP, committed-vote, peer WebSocket and peer UI instrumentation to the normal Cloud browser flow. It does not provision or restore a cluster. It is prepared source; actual Cloud browser execution has not been performed.

## Inputs and execution boundary

- The supported App source is `c2dad254cc407431092691f9ea5892f8324f1520`. `ui-source-contract.json` records the six reviewed frontend blob IDs and the unchanged measurement module hash. A different frontend needs a new compatibility review.
- Copy `plan.TEMPLATE.NOT_ACCEPTED.json` to a private execution input and fill the real HTTPS Origin, actual Release/Digests, Replica/Pool/config/Schema values, existing normal-business/Release/window evidence references and a current execution window. The template remains intentionally unaccepted.
- Release and normal-business evidence come from the activation Run. This package does not independently query the deployment or repeat accepted installation, Pull, TLS, login, monitoring smoke, UI or CNI tests. A reference string is a human evidence binding, not an automatic verification of its contents.
- No A/D test-account or room fixture is needed. During an explicitly requested execution, each private room creates two synthetic Members and one Guest through the normal UI. The default six clients form two rooms; twelve clients form four rooms. Member rows remain after the run because the current App provides no deletion endpoint.
- These are browser clients with serial vote intents per room. Connected clients are not concurrent requests, maximum capacity or a full load/failure acceptance test.

The default command only validates a local plan and never imports Playwright or starts a browser. An incomplete template returns `PENDING_CLOUD_MEASUREMENT_INPUTS` and exit code 2. Real browser execution requires the separate `--execute` flag and a new output directory.

```bash
# Local source tests only; no browser, account or service request.
node --test test_cloud_business.mjs

# Resolve the pinned runtime dependencies. No browser download in this step.
npm ci --ignore-scripts --no-audit --no-fund

# Local validation only. Fill a separate private file after normal Cloud activation.
node run-cloud-measurement.mjs --plan /private/cloud-measurement-plan.json

# Future execution only: use the installed Google Chrome and a NEW result directory.
# Do not run this before the actual normal-business baseline and shared window exist.
node run-cloud-measurement.mjs \
  --plan /private/cloud-measurement-plan.json \
  --execute --channel chrome --out /private/new-cloud-measurement-result
```

Node 20 or later and Playwright `1.63.0` are required. `--channel chrome` uses installed Google Chrome. Without that option, Playwright requires a compatible Chromium installation; browser installation or launch is not implied by dependency installation. Keep `DEBUG` and `PWDEBUG` unset. Do not enable traces, HAR, video or storage-state export for this run.

## Measured path

Each client has a separate browser context with HTTPS validation enabled. Setup uses registration/login, lobby, private room, team, Ready and game start UI. Session state selects the room page; there is no `/rooms/{id}` frontend page route. Setup is excluded from the normal measurement phase. Normal HTTP stays on the approved Origin and normal room/presence/lobby/chat WebSockets stay on approved WSS paths.

The adapter observes an actual UI vote click, the accepted HTTP commit with its game version, an independent peer's `vote.tally_changed` event for that same version, and that peer's rendered tally. Turn numbers and legal empty cells are observed from the UI. The harness sends one vote intent per room turn with no vote retry. Warmup is excluded; six clients, 60 seconds warmup and 180 seconds normal observation are the initial template, not an accepted operational result.

Report only observed values with sample counts and duration:

1. Per-route HTTP p95 and errors within the measured phase, plus first vote-intent error rate.
2. UI vote action start to peer WSS and peer UI p95 for valid samples correlated to the same committed game version. This does not measure from a server commit timestamp.
3. Successful vote intents per observed normal second, connected-client cohort and generator CPU/memory/RSS context.

If the full room-socket cohort or an initial peer frame is absent, a room ends early, the window expires, a normal business step fails or there are no successful correlated normal samples, the receipt remains incomplete. Individual unknown/error samples stay in the error denominator. `summary.cloud_vote_latency` reports the correlated sample count and the two action-start latency percentiles. Generator sampling does not prove available service capacity. Do not present these results as all-user latency, maximum throughput, TH-17 completion, fault tolerance, RTO or RPO.

## Stop, cleanup and output

Setup has a bounded watchdog. Concurrent room tasks settle before cleanup; there are no background vote retries after a run stop. Cleanup makes at most one harness leave attempt per confirmed own room page in Guest, non-host Member, host Member order. Existing UI behavior may retry a stale cleanup request once; the receipt separates that from zero harness vote retries. Setup failure can close the browser before leave is confirmed, so attempted and unconfirmed room cleanup remain explicit. Member deletion is never claimed.

The output directory contains `observations.json`, `summary.json`, `run-receipt.json`, `generator-resources.json` and `SHA256SUMS.json`. IDs are pseudonymized by the reused instrumentation. Raw credentials, cookies, CSRF values, response bodies, WebSocket frames and raw error messages are not exported. The reused summary retains `NOT_RUNTIME_ACCEPTANCE`; a completed scoped observation is not full project acceptance.

## Preparation evidence

The 29 new mocked/synthetic boundary tests passed locally. They cover plan gates, Origin/WSS paths, observed turns/cells, pinned panels, per-client contexts, peer-frame cohort, successful correlated timing gates, stop/settle/cleanup, CLI default non-execution and sanitized output. Playwright dependencies were installed locally. Actual Chrome launch, Cloud UI compatibility, HTTP/WSS traffic, account/room creation and measured numbers are `NOT_RUN`. The existing measurement module was reused unchanged and its completed tests were not repeated.
