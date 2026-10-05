#!/usr/bin/env python3
"""Real isolated synthetic MariaDB backup/restore measurement, never a Cloud DR claim."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid

APP_SHA = "c12b3d15a4dd2c806fac4326a9eb30ed6e8a81b3"
HERE = Path(__file__).resolve().parent
TABLE_KEYS = {
    "alembic_version": "version_num", "member": "member_id", "member_stats": "member_id",
    "game": "game_id", "game_participant": "id", "move": "game_id,turn_no",
    "game_result": "game_id", "rating_history": "id",
}
METRIC_HEADER = "metric_id,baseline,target,actual,unit,aggregation,sample_count,condition_ref,raw_artifact_ref"
TIMELINE_HEADER = "event_id,timestamp_utc,timestamp_kst,actor_ref,event,result,evidence_ref"


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def stamp(t: dt.datetime) -> str:
    return t.isoformat(timespec="microseconds").replace("+00:00", "Z")


def kst(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="microseconds")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def command(args: list[str], *, data: bytes | None = None, stdout=None, check=True) -> subprocess.CompletedProcess:
    # Never print raw CLI diagnostics: keys/paths or SQL rows are private scratch material.
    r = subprocess.run(args, input=data, stdout=stdout or subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=120)
    if check and r.returncode:
        raise RuntimeError(f"{Path(args[0]).name} returned {r.returncode}; private diagnostics withheld")
    return r


class Database:
    def __init__(self, root: Path, transport: str):
        self.root = root
        self.socket = root / "db.sock"
        self.transport = transport
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.process: subprocess.Popen | None = None

    def __enter__(self):
        self.root.mkdir(mode=0o700)
        data_dir = self.root / "data"
        prefix = os.environ.get("SEOKPAN_FIXTURE_TOOL_PREFIX")
        basedir = [f"--basedir={prefix}"] if prefix else []
        user = pwd.getpwuid(os.getuid()).pw_name
        command(["mariadb-install-db", "--no-defaults", *basedir, f"--datadir={data_dir}",
                 "--auth-root-authentication-method=normal", "--skip-test-db", f"--user={user}"])
        self.log = (self.root / "process.log").open("wb")
        try:
            return self.start(data_dir, basedir, user)
        except BaseException:
            self.close()
            raise

    def start(self, data_dir, basedir, user):
        self.data_dir = data_dir
        self.process = subprocess.Popen([
            "mariadbd", "--no-defaults", *basedir, f"--datadir={data_dir}",
            *([f"--socket={self.socket}", "--skip-networking"] if self.transport == "unix"
              else ["--socket=", "--bind-address=127.0.0.1", f"--port={self.port}"]),
            "--skip-log-bin",
            f"--pid-file={self.root / 'db.pid'}", f"--log-error={self.root / 'error.log'}",
            f"--user={user}", "--innodb-buffer-pool-size=32M", "--max-connections=10",
            "--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci",
        ], stdout=self.log, stderr=self.log)
        for _ in range(150):
            if self.process.poll() is not None:
                self.close()
                raise RuntimeError("isolated MariaDB startup failed; private diagnostics withheld")
            r = command(self.cli() + ["-e", "SELECT 1"], check=False)
            if r.returncode == 0:
                self.assert_owned()
                if self.query("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='stone_game'").strip() != b"0":
                    self.close()
                    raise RuntimeError("refusing a reused restore/source target")
                return self
            time.sleep(0.1)
        self.close()
        raise RuntimeError("isolated MariaDB startup timeout")

    def cli(self):
        return ["mariadb", "--no-defaults", *self.connection_options(), "--user=root", "--batch",
                "--skip-column-names", "--default-character-set=utf8mb4"]

    def connection_options(self):
        return (["--protocol=SOCKET", f"--socket={self.socket}"] if self.transport == "unix"
                else ["--protocol=TCP", "--host=127.0.0.1", f"--port={self.port}"])

    def query(self, sql: str) -> bytes:
        self.assert_owned()
        return command(self.cli(), data=("SET time_zone='+00:00';" + sql).encode()).stdout

    def assert_owned(self):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("owned isolated MariaDB process is not alive")
        r = command(self.cli() + ["-e", "SELECT @@datadir,@@port;"])
        fields = r.stdout.decode().strip().split("\t")
        if (len(fields) != 2 or Path(fields[0]).resolve() != self.data_dir.resolve()
                or (self.transport == "tcp" and int(fields[1]) != self.port)
                or self.process.poll() is not None):
            raise RuntimeError("isolated MariaDB process/datadir/transport identity mismatch")

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        if hasattr(self, "log"):
            self.log.close()

    def __exit__(self, *_):
        self.close()


def fixture_sql(games: int) -> str:
    """Deterministic invented rows, no project user data or copied password hash."""
    sql = ["USE stone_game; START TRANSACTION;", "INSERT INTO member VALUES "
           "(1,'fixture_black','FixtureBlack','SYNTHETIC_NOT_A_LOGIN_HASH',1000,'2026-01-01','2026-01-01'),"
           "(2,'fixture_white','FixtureWhite','SYNTHETIC_NOT_A_LOGIN_HASH',1000,'2026-01-01','2026-01-01');",
           f"INSERT INTO member_stats VALUES (1,0,{games},0,{games},'2026-01-01'),(2,0,{games},0,{games},'2026-01-01');"]
    for i in range(1, games + 1):
        game = str(uuid.UUID(int=i))
        # UUIDv4 bits are required by current App read contract; deterministic fixture IDs.
        game = str(uuid.UUID(game, version=4))
        black = str(uuid.UUID(int=games + 2*i, version=4))
        white = str(uuid.UUID(int=games + 2*i + 1, version=4))
        sql.extend([
            f"INSERT INTO game VALUES ('{game}','fixture-room-{i}',5,'COMPLETED','2026-01-01','2026-01-01');",
            f"INSERT INTO game_participant (game_id,team,member_id,is_guest,guest_label,participant_id) VALUES ('{game}','BLACK',1,0,NULL,'{black}'),('{game}','WHITE',2,0,NULL,'{white}');",
            f"INSERT INTO game_result VALUES ('{game}','DRAW','DRAW',1,'2026-01-01');",
            f"INSERT INTO rating_history (member_id,game_id,rating_before,rating_after,rating_delta,recorded_at) VALUES (1,'{game}',1000,1000,0,'2026-01-01'),(2,'{game}',1000,1000,0,'2026-01-01');",
        ])
        moves = 13 if games != 52 or i <= 31 else 12
        for turn in range(1, moves + 1):
            team = "BLACK" if turn % 2 else "WHITE"
            sql.append(f"INSERT INTO move VALUES ('{game}',{turn},{turn},'{team}',{(turn-1)%15},{(turn-1)//15},1,1,'2026-01-01');")
    sql.append("COMMIT;")
    return "\n".join(sql)


def canonical(db: Database) -> dict:
    actual = set(db.query("SELECT table_name FROM information_schema.tables WHERE table_schema='stone_game' ORDER BY table_name;").decode().splitlines())
    if actual != set(TABLE_KEYS):
        raise RuntimeError("unexpected schema table set")
    rows, schemas, counts = {}, {}, {}
    for table, keys in TABLE_KEYS.items():
        schemas[table] = hashlib.sha256(db.query(f"SHOW CREATE TABLE stone_game.{table};")).hexdigest()
        rows[table] = hashlib.sha256(db.query(f"SELECT * FROM stone_game.{table} ORDER BY {keys};")).hexdigest()
        counts[table] = int(db.query(f"SELECT COUNT(*) FROM stone_game.{table};").strip())
    revision = db.query("SELECT version_num FROM stone_game.alembic_version;").decode().strip()
    if revision != "20260902_0002":
        raise RuntimeError("unexpected fixture revision")
    return {"schema_sha256_by_table": schemas, "rows_sha256_by_table": rows,
            "row_counts_by_table": counts, "revision": revision}


def emit_evidence(output: Path, args, started, finished, samples, events, versions, cleanup, failure=None):
    # Exclusive output creation prevents replacing an earlier Run.
    output.mkdir(parents=True, exist_ok=False)
    release = json.loads((HERE / "release_template.json").read_text())
    release.update({
        "run_id": args.run_id, "test_id": "T18", "environment": f"ephemeral {args.transport} local Ubuntu fixture",
        "scope": "partial T17/T18: synthetic MariaDB Dump/gzip/age/local-copy/decrypt/isolated import/data verification only",
        "assigned_execution_owner": "kshi1313-gif (C/Data)",
        "actual_operator": "Codex, user-authorized contribution for tjung03",
        "reviewer": None, "collaborators": [],
        "observed_principal_ref": "temporary isolated fixture root; no project/cloud account",
        "account_management_owner_ref": "temporary harness only; C operational account ownership unchanged",
        "started_at_utc": stamp(started), "finished_at_utc": stamp(finished),
        "started_at_kst": kst(started), "finished_at_kst": kst(finished),
        "conditions_ref": "summary.md#identity-and-scope",
        "raw_artifact_ref": "summary.md#evidence-and-recovery",
        "custodian_ref": "tjung03; public summaries in new Docs Run",
        "access_policy_ref": "synthetic aggregate/hash outputs only; temporary SQL/backup/key never published",
        "retention_ref": "public source/evidence retained; disposable material cleanup result in summary",
        "availability_integrity": "five-file Run/four payload checksums; " + cleanup,
    })
    release["source"].update({"app_sha": APP_SHA, "infra_sha": args.infra_sha})
    release["revisions"]["schema"] = "20260902_0002 (derived fixture DDL; not online Alembic execution)"
    release["revisions"]["tool_manifest"] = sha(HERE / "run.py")
    release["missing_inputs"] = ["ACTUAL_RDS_BACKUP_PATH", "APP_REDIS_CLIENT_RECOVERY", "APPROVED_IMAGES_RELEASE", "C_DATA_REVIEW", "FULL_T18_ACCEPTANCE"]
    if samples:
        last = samples[-1]
        release["recovery"].update({
            "backup_id": last["sample_id"], "encrypted_backup_sha256": last.get("encrypted_backup_sha256"),
            "dump_started_at_utc": last.get("dump_started_at_utc"), "dump_finished_at_utc": last.get("dump_finished_at_utc"),
            "import_started_at_utc": last.get("import_started_at_utc"), "import_finished_at_utc": last.get("import_finished_at_utc"),
        })
    (output / "release.json").write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n")
    with (output / "metrics.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(METRIC_HEADER.split(","))
        for sample in samples:
            for name, value in sample["metrics"].items():
                unit = "bytes" if name.endswith("_bytes") else "seconds"
                w.writerow([f'{sample["sample_id"]}:{name}', "", "", value, unit, "single actual sample", 1,
                            f'summary.md#{sample["sample_id"]}', "summary.md#evidence-and-recovery"])
    with (output / "timeline.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(TIMELINE_HEADER.split(",")); w.writerows(events)
    text = f'''# Isolated synthetic MariaDB partial rehearsal — {args.run_id}

## Identity and Scope

- Test T18, partial T17/T18 Data path; full Acceptance NOT RUN.
- App reference `{APP_SHA}`; fixture DDL is derived from the two approved App revisions, not the operational Migration CLI. No actual project Backup/schema/data was supplied or changed.
- Assigned Data owner 김상희/kshi1313-gif; actual contribution by Codex under tjung03 authorization. C did not execute or review this Run; C Data review pending. A Host and D Image/evidence responsibilities remain unchanged; D index review pending.
- All DBs are newly initialized temporary directories, using `{args.transport}` transport. Unix mode uses `--skip-networking`; TCP mode binds only 127.0.0.1 on separately allocated ephemeral ports and disables the Unix socket. Local disposable fixture root has no password; this is not production TLS/role/account proof. No Docker/registry, project service, AWS/ROSA, S3/VPN, paid environment or Cloud identity used.
- Planned three repeated small samples (52 completed synthetic games/655 moves each), one larger sample (1000 games/13000 moves). Only those two counts match or scale the published precheck; other row composition/bytes are invented, not actual C data. The larger data is a sensitivity case, not forecast production load. No one sample is selected as an operational bound.
- Synthetic identities/rows are invented. The password field is a non-login placeholder: no authentication or App business recovery was tested. The quiescent fixture does not measure concurrent-write consistency or DB load under production traffic.
- Tool versions: `{json.dumps(versions, ensure_ascii=False, sort_keys=True)}`. Package MariaDB version differs from project 11.8.9; no claim of RDS/actual-version parity.

## Results

- Render: NOT RUN; Deployment: NOT RUN; Acceptance: NOT RUN.
- Partial local execution: {"FAIL — sanitized failure type " + failure if failure else "PASS"}; completed samples: {sum(bool(s.get("source_restore_comparison_equal")) for s in samples)}. Completed sample comparisons and negative-case outcomes are recorded below; unfinished steps have no invented success/measurement. Raw metric values are in metrics.csv, not interpreted as RTO/RPO.
- Incident, detection/decision, selected older backup, local OCP/Pod startup, Redis, App Image, client guidance/login/representative business, real S3/network transfer, production workload and cost were not measured. `rto_seconds`, `rpo_seconds`, `incident_at_utc`, `business_resumed_at_utc` remain null.
- UTC/KST wall-clock event timestamps share one host clock. Durations use monotonic perf_counter. No independent clock synchronization uncertainty or real RDS clock was measured.

## Evidence and Recovery

- Five-file Run with four payload checksum entries (checksums.txt does not hash itself); timeline.csv records actual steps, metrics.csv the actual measured durations/bytes. The JSON records below are actual execution comparison results, not source tests. SQL dumps, ciphertext, age identities and private process diagnostics are disposable scratch files and are never published.
- Harness input SHA-256: run.py `{sha(HERE / "run.py")}`, schema.sql `{sha(HERE / "schema.sql")}`, release_template.json `{sha(HERE / "release_template.json")}`.
- For samples reaching Dump, fixture writes finish and COMMIT first and no writer runs afterward; recorded DB UTC bounds bracket that quiescent snapshot. Only completed equal comparisons below prove the deterministic fixture marker/row hashes were restored. Exact operational Data time is unreviewed, so recovery.data_reference_time_utc/data_reference_level remain null and no confirmed RPO is calculated.
- A local file copy on the same machine replaces the real Data VM→S3→On-Prem path. `fixture_data_reference_lower_bound_to_local_complete_seconds` is a conservative quiescent-fixture bracket only, not actual Cloud backup freshness or a configured interval guarantee.
- Cleanup: {cleanup}. Cleanup scope is only harness-owned temporary datadirs/processes/keys/backups; output evidence remains.

'''
    for sample in samples:
        text += f'### {sample["sample_id"]}\n\n```json\n' + json.dumps({k:v for k,v in sample.items() if k != "metrics"}, ensure_ascii=False, indent=2, sort_keys=True) + '\n```\n\n'
    text += '''## Follow-up

- C reviews derived schema, snapshot assumptions, restore comparisons and operational tools/accounts; those reviews are pending and must not be attributed to C before received.
- A confirms actual isolated Host/capacity; B/D prepare preserved current Image/new Redis/client path and combine the full business timeline; no full T18 or deployed Release acceptance follows from this partial Run.
- This evidence may inform implementation burden/bottlenecks in the current restore structure. Real backup transfer/availability, user interruption/loss acceptance, full-business rehearsal, costs/team schedule and adopted design choice remain separate before new targets are finalized.
'''
    (output / "summary.md").write_text(text)
    (output / "checksums.txt").write_text("".join(f"{sha(output / name)}  {name}\n" for name in ["summary.md", "release.json", "metrics.csv", "timeline.csv"]))


def run(args):
    for name in ("mariadbd", "mariadb-install-db", "mariadb", "mariadb-dump", "age", "age-keygen"):
        if shutil.which(name) is None:
            raise RuntimeError(f"required local tool absent: {name}")
    if args.output.exists():
        raise RuntimeError("refusing to overwrite an existing Run")
    started = now(); samples = []; events = []; failure = None
    versions = {"python": os.sys.version.split()[0]}

    def event(sample_id, label, result="OK"):
        t = now(); events.append([f"{sample_id}:{label}", stamp(t), kst(t), "Codex for tjung03", label, result, "summary.md#evidence-and-recovery"])
        return t

    temporary_context = tempfile.TemporaryDirectory(prefix="seokpan-dr-fixture-")
    temporary = temporary_context.name
    try:
      with temporary_context:
        for name in ("mariadbd", "mariadb-dump", "age"):
            versions[name] = command([name, "--version"]).stdout.decode().strip()
        work = Path(temporary); os.chmod(work, 0o700)
        key = work / "age.identity"; wrongkey = work / "wrong.identity"
        command(["age-keygen", "-o", str(key)])
        command(["age-keygen", "-o", str(wrongkey)])
        os.chmod(key, 0o600); os.chmod(wrongkey, 0o600)
        recipient = command(["age-keygen", "-y", str(key)]).stdout.decode().strip()
        for index, games in enumerate((52, 52, 52, 1000), 1):
            sample_id = f"fixture-{index}"; sample = {"sample_id": sample_id, "synthetic_games": games, "metrics": {}}
            samples.append(sample)
            sample_work = work / sample_id; sample_work.mkdir(mode=0o700)
            event(sample_id, "isolated_source_prepare_start")
            with Database(sample_work / "source", args.transport) as source:
                source.query((HERE / "schema.sql").read_text() + fixture_sql(games))
                event(sample_id, "fixture_committed_writes_quiesced")
                baseline = canonical(source)
                lower = source.query("SELECT UTC_TIMESTAMP(6);").decode().strip()
                lower = dt.datetime.fromisoformat(lower).replace(tzinfo=dt.timezone.utc)
                event(sample_id, "data_reference_lower_bound")
                dump = sample_work / "dump.sql"
                sample["dump_started_at_utc"] = stamp(event(sample_id, "dump_start")); t = time.perf_counter()
                source.assert_owned()
                with dump.open("wb") as f:
                    command(["mariadb-dump", "--no-defaults", *source.connection_options(), "--user=root",
                             "--single-transaction", "--quick", "--routines", "--events", "--triggers", "--skip-comments",
                             "--hex-blob", "--default-character-set=utf8mb4", "--databases", "stone_game"], stdout=f)
                sample["metrics"]["dump_seconds"] = time.perf_counter()-t
                sample["dump_finished_at_utc"] = stamp(event(sample_id, "dump_finish"))
                upper = source.query("SELECT UTC_TIMESTAMP(6);").decode().strip()
                sample["data_reference_lower_bound_utc"] = stamp(lower)
                sample["data_reference_upper_bound_utc"] = stamp(dt.datetime.fromisoformat(upper).replace(tzinfo=dt.timezone.utc))
                sample["metrics"]["dump_bytes"] = dump.stat().st_size
                compressed = sample_work / "dump.sql.gz"; event(sample_id,"gzip_start"); t=time.perf_counter()
                with dump.open("rb") as fin, gzip.open(compressed,"wb",compresslevel=6) as fout:
                    shutil.copyfileobj(fin,fout)
                sample["metrics"]["gzip_seconds"]=time.perf_counter()-t; event(sample_id,"gzip_finish")
                ciphertext=sample_work/"backup.age"; event(sample_id,"age_encrypt_start"); t=time.perf_counter()
                command(["age","-r",recipient,"-o",str(ciphertext),str(compressed)])
                sample["metrics"]["age_encrypt_seconds"]=time.perf_counter()-t; event(sample_id,"age_encrypt_finish")
                sample["metrics"]["encrypted_bytes"]=ciphertext.stat().st_size
                partial=sample_work/"local.partial"; local=sample_work/"local.age"; t=time.perf_counter()
                event(sample_id,"local_copy_start"); shutil.copyfile(ciphertext,partial)
                with partial.open("rb") as f: os.fsync(f.fileno())
                if sha(partial)!=sha(ciphertext): raise RuntimeError("copied ciphertext checksum mismatch")
                partial.replace(local)
                fd=os.open(sample_work,os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
                sample["metrics"]["local_atomic_copy_verify_seconds"]=time.perf_counter()-t
                complete=event(sample_id,"backup_local_complete")
                sample["metrics"]["fixture_data_reference_lower_bound_to_local_complete_seconds"]=(complete-lower).total_seconds()
                sample["encrypted_backup_sha256"]=sha(local)
                wrongout=sample_work/"wrong.out"
                if command(["age","-d","-i",str(wrongkey),"-o",str(wrongout),str(local)],check=False).returncode==0:
                    raise RuntimeError("wrong age identity was accepted")
                truncated=sample_work/"truncated.age"; truncated.write_bytes(local.read_bytes()[:local.stat().st_size//2])
                if command(["age","-d","-i",str(key),"-o",str(sample_work/"truncated.out"),str(truncated)],check=False).returncode==0:
                    raise RuntimeError("truncated ciphertext was accepted")
                sample["wrong_key_rejected"]=True; sample["truncated_ciphertext_rejected"]=True
                event(sample_id,"backup_negative_cases_verified")
                decrypted=sample_work/"restored.sql.gz"; event(sample_id,"age_decrypt_start"); t=time.perf_counter()
                command(["age","-d","-i",str(key),"-o",str(decrypted),str(local)])
                if sha(decrypted)!=sha(compressed): raise RuntimeError("decrypted payload checksum mismatch")
                sample["metrics"]["age_decrypt_verify_seconds"]=time.perf_counter()-t; event(sample_id,"age_decrypt_finish")
                restoredsql=sample_work/"restored.sql"; event(sample_id,"gunzip_start"); t=time.perf_counter()
                with gzip.open(decrypted,"rb") as fin, restoredsql.open("wb") as fout: shutil.copyfileobj(fin,fout)
                if sha(restoredsql)!=sha(dump): raise RuntimeError("uncompressed SQL checksum mismatch")
                sample["metrics"]["gunzip_verify_seconds"]=time.perf_counter()-t; event(sample_id,"gunzip_finish")
                event(sample_id,"new_isolated_restore_target_prepare_start"); t=time.perf_counter()
                with Database(sample_work/"restore", args.transport) as target:
                    sample["metrics"]["new_restore_target_prepare_seconds"]=time.perf_counter()-t
                    event(sample_id,"new_isolated_restore_target_ready")
                    sample["import_started_at_utc"]=stamp(event(sample_id,"import_start")); t=time.perf_counter()
                    target.query(restoredsql.read_text())
                    sample["metrics"]["import_seconds"]=time.perf_counter()-t
                    sample["import_finished_at_utc"]=stamp(event(sample_id,"import_finish"))
                    event(sample_id,"data_verify_start"); t=time.perf_counter(); restored=canonical(target)
                    if restored!=baseline: raise RuntimeError("restored schema/rows/counts/revision differ")
                    sample["metrics"]["schema_rows_revision_verify_seconds"]=time.perf_counter()-t
                    sample["verification"] = restored
                    sample["source_restore_comparison_equal"] = True
                    event(sample_id,"data_verify_finish")
                # Refuse reuse even if a caller attempts another start on the restored datadir.
                try:
                    with Database(sample_work/"restore", args.transport): pass
                except FileExistsError:
                    sample["reused_target_rejected"] = True
                else:
                    raise RuntimeError("reused target was accepted")
            event(sample_id,"sample_processes_stopped")
        event("run","temporary_cleanup_start")
    except BaseException as e:
        failure = type(e).__name__
        event("run", "partial_execution_failed", failure)
    if Path(temporary).exists():
        failure = failure or "CleanupIncomplete"
        cleanup = "INCOMPLETE: harness-owned temporary material remains private; operator cleanup required"
        event("run", "temporary_cleanup_incomplete", "FAIL")
    else:
        cleanup="all temporary datadirs/processes/keys/backups removed"
        event("run","temporary_cleanup_verified")
    finished=now()
    emit_evidence(args.output,args,started,finished,samples,events,versions,cleanup,failure)
    print(json.dumps({"run_id":args.run_id,"partial_execution":"FAIL" if failure else "PASS","error_type":failure,"full_acceptance":"NOT RUN",
                      "samples":len(samples),"rto_seconds":None,"rpo_seconds":None,
                      "cleanup":"incomplete" if cleanup.startswith("INCOMPLETE") else "verified"},sort_keys=True))
    if args.print_evidence:
        for name in ("summary.md","release.json","metrics.csv","timeline.csv","checksums.txt"):
            print(f"BEGIN_EVIDENCE_FILE {name}\n{(args.output/name).read_text()}END_EVIDENCE_FILE {name}")
    if failure:
        raise SystemExit(1)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--run-id",required=True)
    p.add_argument("--infra-sha",default=None)
    p.add_argument("--transport", choices=("unix", "tcp"), default="unix")
    p.add_argument("--print-evidence",action="store_true")
    args=p.parse_args()
    if not args.run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in args.run_id):
        p.error("run-id must use letters, numbers, dash, dot or underscore")
    if args.infra_sha is not None and (len(args.infra_sha)!=40 or any(c not in "0123456789abcdef" for c in args.infra_sha)):
        p.error("infra-sha must be a complete lowercase commit SHA")
    def interrupted(_signum, _frame):
        raise InterruptedError("fixture execution interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    try: run(args)
    except Exception as e:
        print(json.dumps({"partial_execution":"FAIL","error_type":type(e).__name__,"detail":str(e)},sort_keys=True))
        raise SystemExit(1) from None


if __name__=="__main__": main()
