# Isolated synthetic backup/restore rehearsal

This is a user-authorized contribution to C/Data work. Assigned Data ownership remains
김상희 (`kshi1313-gif`); the actual execution operator must be recorded accurately.
Codex acting for `tjung03` does not imply C performed or reviewed the work. C Data review
and D evidence index review are pending. A Host and D Image responsibilities are unchanged.

The tool measures actual disposable MariaDB CLI Dump, gzip, age encryption, same-machine
atomic local copy/hash verification, decryption, new isolated DB import, and comparison of
all schema definitions, ordered row hashes, row counts and Alembic revision. It runs three
52-game/655-move synthetic samples and one 1000-game/13000-move sensitivity sample. The
small game's/move's counts match the published C precheck, but other rows, distribution
and Dump/storage bytes are invented; it does not reproduce C's approximately 0.36MB DB.
These are not production volume forecasts. No user rows, project credentials, existing backups or Cloud services
are used.

## Inputs and isolation

- Python 3.12 or later, MariaDB CLI/server/install-db, `age` and `age-keygen` on PATH.
- `schema.sql` derives the fixture DDL from App commit
  `c12b3d15a4dd2c806fac4326a9eb30ed6e8a81b3`, revisions `20260901_0001` and
  `20260902_0002`: seven business tables plus `alembic_version`. It is not the online
  migration gate. No Source or Schema change is proposed for App.
- Each source and restore DB uses a newly created temporary datadir. Unix mode uses a
  private Unix socket with networking disabled; TCP mode binds only loopback, disables
  the Unix socket and uses a separately allocated ephemeral port. It rejects reused
  targets and existing output Runs. Never point it at an existing service.
- TCP mode's temporary fixture root has no password. Neither transport is proof of the
  production TLS, hostname, purpose-account or least-privilege contract.
- Age identities, invented SQL rows, dumps, ciphertext and process diagnostics remain
  in temporary private scratch and are removed in `finally`/context cleanup. Raw contents
  are never printed. Cleanup stops only child processes created by this tool.
- A locally extracted package prefix may be supplied via `SEOKPAN_FIXTURE_TOOL_PREFIX`;
  it must be that package tree's `usr` directory. PATH and LD_LIBRARY_PATH are set by
  the operator. The tool does not install packages or start system services.

## Execution

```bash
python3 tools/recovery_fixture/run.py \
  --run-id fixture-20261005-01 \
  --output evidence/T18/fixture-20261005-01
```

If Unix sockets are unavailable, add `--transport tcp`. On a committed CI checkout add
`--infra-sha "$GITHUB_SHA"`; the complete lowercase 40-character SHA is validated.
`--print-evidence` prints only the five public evidence files, for retrieval from the
original Source CI Run without artifact/cache uploading. The output directory must not
exist. A failed actual execution also emits a new five-file Run with measured partial
events/metrics and sanitized failure type, then exits nonzero. Preflight refusal does
not modify an existing Run. Preserve the original failed execution before a new Run/retest. The tool never declares full T18
acceptance and does not publish to Docs or change the evidence index itself.

## Evidence and limits

The output uses the existing Docs five-file Run format: `summary.md`, `release.json`,
`metrics.csv`, `timeline.csv`, `checksums.txt`. `completeness` remains `INCOMPLETE`,
Render/Deployment/Acceptance remain `NOT RUN`, and reviewer remains null until actually
reviewed. Do not relabel the successful partial commands as T17/T18 acceptance.

The deterministic fixture is committed before Dump and writes are quiesced. A DB-clock
bracket and restored marker/hash comparison support this fixture only. Data time has
not been reviewed as an operational timestamp: `data_reference_time_utc`,
`data_reference_level`, incident/business times and RTO/RPO remain null. Wall-clock UTC
and KST timestamps use one host; elapsed measurements use a monotonic clock.

Wrong identity and truncated ciphertext must be rejected. The valid payload is then
decrypted and imported into another fresh DB. All schema and ordered row hashes/counts
must equal the source. A local file copy does not measure RDS→S3→On-Prem network delay,
failure/retry behavior, backup freshness during a real schedule, concurrent write load,
or operator availability.

MariaDB package/version/OS can differ from the project MariaDB 11.8.9 and RDS target;
the actual versions are recorded. No App/Redis/Image/client business recovery is measured.
The synthetic password field is deliberately not an authentication hash. It also does
not measure a full DB host boot, OCP scheduling, user guidance or production costs.

Use the partial evidence to identify current restore-path burden and bottlenecks. Before
new DR goals/backup interval/structure are finalized, compare the relevant full business
timeline, data-loss/client scope, practical team schedule/cost and actual backup path;
record the adopted choice and reflect the affected design/code/diagrams consistently.
