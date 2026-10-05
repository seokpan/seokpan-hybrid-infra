#!/usr/bin/env python3
"""Owned loopback Backup/Restore + TLS/AUTH Backend business rehearsal, partial T18."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
DATA_HERE = HERE.parent / "recovery_fixture"
APP_SHA = "c12b3d15a4dd2c806fac4326a9eb30ed6e8a81b3"
APP_MANIFEST_SHA256 = "acaaba45e093c68d59890a13575543f94a44eb5ddd51fc59da31468d2beabb07"
spec = importlib.util.spec_from_file_location("data_fixture", DATA_HERE / "run.py")
data = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = data
spec.loader.exec_module(data)
probe_spec = importlib.util.spec_from_file_location("local_business_probe", HERE / "business_probe.py")
probe = importlib.util.module_from_spec(probe_spec)
sys.modules[probe_spec.name] = probe
probe_spec.loader.exec_module(probe)

def verify_app(checkout):
    manifest = checkout / "source-blob-manifest.json"
    if manifest.is_file():
        if data.sha(manifest) != APP_MANIFEST_SHA256:
            raise RuntimeError("App tree manifest differs from pinned GitHub read")
        tree = json.loads(manifest.read_text())
        if tree["commit"] != APP_SHA or len(tree["entries"]) != 390:
            raise RuntimeError("App source provenance is incomplete")
        for entry in tree["entries"]:
            raw = (checkout / entry["path"]).read_bytes()
            digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if digest != entry["sha"]:
                raise RuntimeError("App source blob changed after provenance read")
        return "390 pinned repository blobs verified"
    head = data.command(["git", "-C", str(checkout), "rev-parse", "HEAD"]).stdout.decode().strip()
    if head != APP_SHA or data.command(["git", "-C", str(checkout), "diff", "--quiet", "HEAD"], check=False).returncode:
        raise RuntimeError("App checkout must match unchanged pinned main")
    return "git HEAD and tracked content verified"

def port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]

def certificates(root):
    root.mkdir(mode=0o700)
    ca_key, ca, key, request, cert = (root / name for name in ("ca.key","ca.crt","server.key","server.csr","server.crt"))
    data.command(["openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1",
                  "-keyout",str(ca_key),"-out",str(ca),"-subj","/CN=Synthetic DR fixture CA",
                  "-addext","basicConstraints=critical,CA:TRUE","-addext","keyUsage=critical,keyCertSign,cRLSign"])
    data.command(["openssl","req","-new","-newkey","rsa:2048","-nodes","-keyout",str(key),
                  "-out",str(request),"-subj","/CN=localhost"])
    extensions = root / "server.ext"
    extensions.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
    data.command(["openssl","x509","-req","-in",str(request),"-CA",str(ca),"-CAkey",str(ca_key),
                  "-CAcreateserial","-days","1","-out",str(cert),"-extfile",str(extensions)])
    for path in root.iterdir(): os.chmod(path,0o600)
    return ca, cert, key

class TLSDatabase(data.Database):
    def __init__(self, root, tls):
        super().__init__(root,"tcp")
        self.tls = tls

    def start(self, data_dir, basedir, user):
        self.data_dir = data_dir
        ca, cert, key = self.tls
        self.process = subprocess.Popen([
            "mariadbd","--no-defaults",*basedir,f"--datadir={data_dir}","--socket=",
            "--bind-address=127.0.0.1",f"--port={self.port}","--skip-log-bin",
            f"--pid-file={self.root / 'db.pid'}",f"--log-error={self.root / 'error.log'}",
            f"--user={user}","--innodb-buffer-pool-size=32M","--max-connections=20",
            "--character-set-server=utf8mb4","--collation-server=utf8mb4_unicode_ci",
            f"--ssl-ca={ca}",f"--ssl-cert={cert}",f"--ssl-key={key}",
        ],stdout=self.log,stderr=self.log)
        for _ in range(150):
            if self.process.poll() is not None:
                raise RuntimeError("owned TLS DB process failed startup")
            result = data.command(self.cli()+["-e","SELECT 1"],check=False)
            if result.returncode == 0:
                self.assert_owned()
                if self.query("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='stone_game'").strip()!=b"0":
                    raise RuntimeError("refusing reused TLS DB")
                if self.query("SELECT @@have_ssl").strip()!=b"YES":
                    raise RuntimeError("TLS DB is not TLS capable")
                return self
            time.sleep(0.1)
        raise RuntimeError("owned TLS DB startup timeout")

def stop(process):
    if process is None: return
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

def accounts(db, identity_password, game_password):
    db.assert_owned()
    db.query(
        f"CREATE USER 'identity_svc'@'127.0.0.1' IDENTIFIED BY '{identity_password}' REQUIRE SSL;"
        f"CREATE USER 'game_svc'@'127.0.0.1' IDENTIFIED BY '{game_password}' REQUIRE SSL;"
        "GRANT SELECT,INSERT ON stone_game.member TO 'identity_svc'@'127.0.0.1';"
        "GRANT SELECT(member_id,nickname,rating),UPDATE(rating) ON stone_game.member TO 'game_svc'@'127.0.0.1';"
        + "".join(f"GRANT SELECT,INSERT,UPDATE,DELETE ON stone_game.{table} TO 'game_svc'@'127.0.0.1';"
                  for table in ("member_stats","game","game_participant","move","game_result","rating_history"))
    )

def check_tools(args):
    if "SSLKEYLOGFILE" in os.environ:
        raise RuntimeError("TLS key logging is not permitted in this owned fixture")
    for name in ("mariadbd","mariadb-install-db","mariadb","mariadb-dump","age","age-keygen","openssl","redis-server"):
        if shutil.which(name) is None: raise RuntimeError(f"required fixture tool absent: {name}")
    verify_app(args.app_checkout)
    python = args.app_checkout / "backend/.venv/bin/python"
    raw = data.command([str(python),"-c",
        "import sys; import importlib.metadata as m; "
        "expected=sys.version_info[:3]==(3,13,15) and m.version('redis')=='8.1.0' and m.version('asyncmy')=='0.2.14'; expected or (_ for _ in ()).throw(RuntimeError('Pinned runtime mismatch')); "
        "from seokpan.production_app import create_production_app; print('exact frozen interpreter available')"]).stdout
    return python,raw.decode().strip()

def emit(args, started, finished, events, metrics, result, versions, cleanup, failure):
    args.output.mkdir(parents=True,exist_ok=False)
    release = json.loads((DATA_HERE/"release_template.json").read_text())
    release.update({
        "run_id":args.run_id,"test_id":"T18","environment":"owned ephemeral loopback TCP TLS processes",
        "scope":"partial synthetic Backup/Restore + new Redis TLS/AUTH + production Backend HTTPS login/ranking/new-game",
        "assigned_execution_owner":"kshi1313-gif C/Data; tjung03 B/App; cyj200115-prog D/evidence",
        "actual_operator":"Codex, user-authorized contribution for tjung03","reviewer":None,
        "collaborators":[],
        "observed_principal_ref":"fixture-owned root plus disposable purpose-scoped SSL-required service accounts",
        "account_management_owner_ref":"fixture-owned disposable accounts; operational C ownership unchanged",
        "started_at_utc":data.stamp(started),"finished_at_utc":data.stamp(finished),
        "started_at_kst":data.kst(started),"finished_at_kst":data.kst(finished),
        "conditions_ref":"summary.md#scope","raw_artifact_ref":"summary.md#results",
        "custodian_ref":"tjung03; source and aggregate evidence only",
        "access_policy_ref":"synthetic checks/hashes/timing only; no private SQL/backup/key/cookies",
        "retention_ref":"public evidence retained; disposable runtime material cleanup status: "+cleanup,
        "availability_integrity":cleanup,
    })
    release["source"].update({"app_sha":APP_SHA,"infra_sha":args.infra_sha})
    release["revisions"].update({"schema":"20260902_0002 derived fixture DDL","tool_manifest":data.sha(HERE/"run.py")})
    release["missing_inputs"]=["C_OPERATIONAL_DATA_REVIEW","A_REAL_HOST","D_APPROVED_IMAGES",
       "FE_BROWSER_WSS_PATH","REAL_RDS_S3_VPN","HISTORICAL_RESULT_HTTP","FULL_T18_ACCEPTANCE"]
    (args.output/"release.json").write_text(json.dumps(release,ensure_ascii=False,indent=2)+"\n")
    with (args.output/"metrics.csv").open("w",newline="") as stream:
        writer=csv.writer(stream);writer.writerow(data.METRIC_HEADER.split(","))
        for name,value in metrics.items():
            writer.writerow([name,"","",value,"seconds" if not name.endswith("_bytes") else "bytes",
                            "single synthetic execution",1,"summary.md#scope","summary.md#results"])
    with (args.output/"timeline.csv").open("w",newline="") as stream:
        writer=csv.writer(stream);writer.writerow(data.TIMELINE_HEADER.split(","));writer.writerows(events)
    summary = f"""# Synthetic Backend business continuation — {args.run_id}

## Scope

- Partial T18 only. App source {APP_SHA}; source verified without modifying App. No approved Image/Release, OCP/Pod, Harbor/PVC, actual RDS/S3/VPN, operator detection/decision, Host guidance, browser/FE/WSS or service RTO/RPO achievement.
- Data owner C/김상희, App B/정태훈, Image/evidence D/최유준, real Host A/이유빈 remain responsible. Actual execution is Codex contribution authorized by tjung03. None of those teammates performed/reviewed this Run unless their separate review is recorded.
- All source/restore datadirs and Redis are new temporary owned processes, loopback only. Redis plaintext port is 0; TLS/AUTH is required. Python verifies explicit CA and localhost hostname to DB/Redis/HTTPS Backend. Fixture root access is local administrative plumbing, not proof of production account/host isolation.
- Dedicated synthetic identity_svc/game_svc accounts require SSL; Passwords, age identities, SQL dumps, ciphertext, sessions and process logs remain private temporary material intended for removal. Actual cleanup status is recorded below; removal is not claimed when cleanup is incomplete. Operational C credentials, storage policy, supported tool/version and Host capacity are unverified.
- A quiescent fictional dataset uses the approved eight-table DDL. Its invented draws/moves are row-comparison fixtures, not proof of semantically valid historical games. Existing completed row counts/hashes and rating values are checked without rebuilding old Redis rooms.
- Prepared Backup exists before scripted continuation begins. Timings use monotonic clock, UTC/KST use one host wall clock. The scripted continuation starts at decrypt/preparation and excludes human detection/decision/wait/guidance. Its elapsed time must not be labeled service RTO.
- Versions: {json.dumps(versions,sort_keys=True)}. MariaDB and Redis versions are fixture package versions and may differ from actual project versions.
- Tool hashes: run.py {data.sha(HERE/"run.py")}; business_probe.py {data.sha(HERE/"business_probe.py")}; Data helper {data.sha(DATA_HERE/"run.py")}; derived schema {data.sha(DATA_HERE/"schema.sql")}.
- Cleanup: {cleanup}.

## Results

Partial execution: {"FAIL, sanitized type "+failure if failure else "PASS"}. Deployment/Release/full T18 Acceptance: NOT RUN.
rto_seconds/rpo_seconds/data_reference_time_utc/incident_at_utc/business_resumed_at_utc remain null.

```json
{json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)}
```

## Functional limit and follow-up

The current GET /api/v1/games/{{old-id}}/result first requires Redis participation, current Room and current/last Game. A fresh empty Redis does not provide that old Room association, although completed DB rows survive. Thus SQL/hash/ranking checks do not prove historical individual-result access for the specified client. The minimum design clarification distinguishes preserved historical records/relations checked by operator SQL, restored member ranking/rating visible to a fresh client, and new-game completion/result visible in the new Runtime. B and the design review must explicitly confirm this scope. This fixture does not add a History feature, invent old Redis state, or present SQL as historical-result client access.

C reviews dataset/snapshot/accounts/TLS/restore conclusions; B reviews historical read and transient-state policy; A confirms actual isolated Host/storage; D reviews Image and new Run/index. Current architecture choice may use these partial measurements, but retained service targets must be supported by an adequately scoped combined rehearsal, user impact/cost/schedule decision and adopted design artifacts.
"""
    (args.output/"summary.md").write_text(summary)
    names=("summary.md","release.json","metrics.csv","timeline.csv")
    (args.output/"checksums.txt").write_text("".join(f"{data.sha(args.output/name)}  {name}\n" for name in names))

def run(args):
    if args.output.exists(): raise RuntimeError("Refusing to overwrite existing Run")
    python,_ = check_tools(args)
    started=data.now();events=[];metrics={};result={};versions={};failure=None;temporary_name=None
    def event(label,outcome="OK"):
        timestamp=data.now()
        events.append([label,data.stamp(timestamp),data.kst(timestamp),"Codex for tjung03",label,outcome,"summary.md#results"])
    app_process=redis_process=None
    app_log=redis_log=app_listener=None
    cleanup_errors=[]
    try:
      with tempfile.TemporaryDirectory(prefix="seokpan-business-fixture-") as temporary_name:
        try:
            work=Path(temporary_name);os.chmod(work,0o700)
            for name in ("mariadbd","redis-server","age","openssl"):
                versions[name]=data.command([name,"version" if name=="openssl" else "--version"]).stdout.decode().strip()
            versions["python"]=data.command([str(python),"--version"]).stdout.decode().strip()
            tls=certificates(work/"tls"); ca,cert,key=tls
            login_password=secrets.token_hex(16)
            hashed = data.command([str(python),"-c",
                "from argon2 import PasswordHasher; import sys; print(PasswordHasher().hash(sys.stdin.read()))"],
                data=login_password.encode()).stdout.decode().strip()
            age_key=work/"age.identity"; data.command(["age-keygen","-o",str(age_key)]);os.chmod(age_key,0o600)
            recipient=data.command(["age-keygen","-y",str(age_key)]).stdout.decode().strip()
            event("isolated_source_prepare_start")
            with TLSDatabase(work/"source",tls) as source:
                source.query((DATA_HERE/"schema.sql").read_text()+data.fixture_sql(52)+
                    f"UPDATE stone_game.member SET password_hash='{hashed}';")
                baseline=data.canonical(source); event("quiescent_synthetic_members_committed")
                dump=work/"dump.sql";t=time.perf_counter();source.assert_owned()
                with dump.open("wb") as stream:
                    data.command(["mariadb-dump","--no-defaults",*source.connection_options(),"--user=root",
                        "--single-transaction","--quick","--routines","--events","--triggers","--skip-comments",
                        "--hex-blob","--default-character-set=utf8mb4","--databases","stone_game"],stdout=stream)
                metrics["dump_seconds"]=time.perf_counter()-t;metrics["dump_bytes"]=dump.stat().st_size
                compressed=work/"dump.gz"
                with dump.open("rb") as inp,gzip.open(compressed,"wb") as out:shutil.copyfileobj(inp,out)
                ciphertext=work/"local.age";data.command(["age","-r",recipient,"-o",str(ciphertext),str(compressed)])
                result["encrypted_backup_sha256"]=data.sha(ciphertext);event("backup_prepared_locally")
                recovery_start=time.perf_counter();event("scripted_continuation_start_not_incident")
                decrypted=work/"restored.gz";t=time.perf_counter()
                data.command(["age","-d","-i",str(age_key),"-o",str(decrypted),str(ciphertext)])
                if data.sha(decrypted)!=data.sha(compressed):raise RuntimeError("decrypt verification failed")
                metrics["decrypt_verify_seconds"]=time.perf_counter()-t;event("decrypt_verified")
                with TLSDatabase(work/"restore",tls) as target:
                    t=time.perf_counter()
                    with gzip.open(decrypted,"rt") as stream:target.query(stream.read())
                    metrics["import_seconds"]=time.perf_counter()-t
                    restored=data.canonical(target)
                    if restored!=baseline:raise RuntimeError("restored canonical comparison failed")
                    result["source_restore_equal"]=True;result["restored_row_counts"]=restored["row_counts_by_table"]
                    event("new_isolated_database_restored_verified")
                    identity_password,game_password,redis_password=(secrets.token_hex(24) for _ in range(3))
                    accounts(target,identity_password,game_password);event("fixture_ssl_required_role_accounts_ready")
                    redis_port=port()
                    app_listener=socket.socket()
                    app_listener.bind(("127.0.0.1",0))
                    app_listener.listen(128)
                    app_port=app_listener.getsockname()[1]
                    redis_dir=work/"redis";redis_dir.mkdir(mode=0o700)
                    redis_config=redis_dir/"redis.conf"
                    redis_config.write_text(f"bind 127.0.0.1\nport 0\ntls-port {redis_port}\ntls-cert-file {cert}\ntls-key-file {key}\ntls-ca-cert-file {ca}\ntls-auth-clients no\nrequirepass {redis_password}\ndir {redis_dir}\nsave \"\"\nappendonly no\nprotected-mode yes\n")
                    os.chmod(redis_config,0o600);redis_log=(work/"redis-process.log").open("wb")
                    redis_process=subprocess.Popen(["redis-server",str(redis_config)],stdout=redis_log,stderr=redis_log)
                    app_env=os.environ.copy()
                    for name in tuple(app_env):
                        if name.startswith("SEOKPAN_") and name!="SEOKPAN_FIXTURE_TOOL_PREFIX":app_env.pop(name,None)
                    app_env.update({
                        "SEOKPAN_ENVIRONMENT":"production","SEOKPAN_CONNECTION_PROFILE":"recovery",
                        "SEOKPAN_DATABASE_EXPECTED_HOST":"localhost","SEOKPAN_DATABASE_EXPECTED_PORT":str(target.port),
                        "SEOKPAN_DATABASE_EXPECTED_NAME":"stone_game","SEOKPAN_DATABASE_CA_FILE":str(ca),
                        "SEOKPAN_IDENTITY_DATABASE_URL":f"mysql+asyncmy://identity_svc:{identity_password}@localhost:{target.port}/stone_game",
                        "SEOKPAN_GAME_DATABASE_URL":f"mysql+asyncmy://game_svc:{game_password}@localhost:{target.port}/stone_game",
                        "SEOKPAN_REDIS_URL":f"rediss://localhost:{redis_port}/0",
                        "SEOKPAN_REDIS_EXPECTED_HOST":"localhost","SEOKPAN_REDIS_EXPECTED_PORT":str(redis_port),
                        "SEOKPAN_REDIS_EXPECTED_DATABASE":"0","SEOKPAN_REDIS_AUTH_TOKEN":redis_password,
                        "SEOKPAN_REDIS_CA_FILE":str(ca),"SEOKPAN_ALLOWED_ORIGINS":json.dumps([f"https://localhost:{app_port}"]),
                        "SEOKPAN_INSTANCE_ID":"independent-synthetic-recovery",
                        "SEOKPAN_FIXTURE_REDIS_PID":str(redis_process.pid),
                        "SEOKPAN_FIXTURE_REDIS_DIR":str(redis_dir),
                    })
                    # Redis must be reachable before App starts; authenticated DB0 must be fresh.
                    redis_probe_code="import asyncio,os\nfrom redis.asyncio import Redis\nasync def main():\n    c=Redis.from_url(os.environ['SEOKPAN_REDIS_URL'],password=os.environ['SEOKPAN_REDIS_AUTH_TOKEN'],ssl_ca_certs=os.environ['SEOKPAN_REDIS_CA_FILE'],ssl_check_hostname=True)\n    try:\n        if (await c.info('server'))['process_id']!=int(os.environ['SEOKPAN_FIXTURE_REDIS_PID']): raise RuntimeError('Owned Redis PID mismatch')\n        if (await c.config_get('dir'))['dir']!=os.environ['SEOKPAN_FIXTURE_REDIS_DIR']: raise RuntimeError('Owned Redis dir mismatch')\n        if await c.ping() is not True: raise RuntimeError('Redis ping failed')\n        if await c.dbsize()!=0: raise RuntimeError('Redis is not empty')\n    finally:\n        await c.aclose()\nasyncio.run(main())\nprint('new Redis authenticated TLS DB0 empty')\n\n"
                    for _ in range(30):
                        if redis_process.poll() is not None:raise RuntimeError("owned new Redis failed")
                        # Separate helper subprocess inherits only explicit fixture env.
                        check=subprocess.run([str(python),"-c",redis_probe_code],env=app_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=3)
                        if check.returncode==0:break
                        time.sleep(0.1)
                    else:raise RuntimeError("new Redis TLS/AUTH probe failed")
                    result["new_redis_tls_auth_empty"]=True;event("new_redis_tls_auth_empty_verified")
                    app_log=(work/"app-process.log").open("wb");t=time.perf_counter()
                    app_process=subprocess.Popen([str(python),"-m","uvicorn","seokpan.app:app",
                        "--fd",str(app_listener.fileno()),"--workers","1","--no-access-log",
                        "--ssl-keyfile",str(key),"--ssl-certfile",str(cert)],cwd=args.app_checkout/"backend",
                        env=app_env,stdout=app_log,stderr=app_log,pass_fds=(app_listener.fileno(),))
                    context=ssl.create_default_context(cafile=ca)
                    readiness_opener=probe.Client(f"https://localhost:{app_port}",context).opener
                    for _ in range(100):
                        if app_process.poll() is not None:raise RuntimeError("owned production Backend failed")
                        try:
                            with readiness_opener.open(f"https://localhost:{app_port}/health/ready",timeout=1) as response:
                                if response.status==200:break
                        except (OSError,urllib.error.URLError):pass
                        time.sleep(0.1)
                    else:raise RuntimeError("production Backend readiness timed out")
                    if app_process.poll() is not None:raise RuntimeError("owned Backend ended after readiness")
                    result["app_owned_inherited_loopback_socket_verified"]=True
                    metrics["app_start_to_ready_seconds"]=time.perf_counter()-t;event("production_backend_https_ready")
                    t=time.perf_counter()
                    result["business_probe"]=probe.run({
                        "base_url":f"https://localhost:{app_port}","ca_file":str(ca),
                        "members":[{"login_id":"fixture_black","password":login_password},
                                   {"login_id":"fixture_white","password":login_password}],
                        "expected_ratings":{"1":1000,"2":1000},
                    })
                    new_game=result["business_probe"]["new_completed_game_id"]
                    from uuid import UUID
                    if str(UUID(new_game))!=new_game:raise RuntimeError("Unexpected fixture UUID")
                    target.assert_owned()
                    new_result=target.query(f"SELECT g.status,r.end_reason,r.winner,r.reflected_to_stats FROM stone_game.game g JOIN stone_game.game_result r USING(game_id) WHERE g.game_id='{new_game}';").decode().strip()
                    if new_result!="COMPLETED\tFORFEIT\tBLACK\t1":raise RuntimeError("New completed SQL result mismatch")
                    if target.query("SELECT member_id,rating FROM stone_game.member ORDER BY member_id;").strip()!=b"1\t1016\n2\t984":raise RuntimeError("New SQL rating reflection mismatch")
                    result["new_completion_sql_and_rating_verified"]=True
                    metrics["https_business_probe_seconds"]=time.perf_counter()-t
                    metrics["scripted_continuation_seconds"]=time.perf_counter()-recovery_start
                    result["historical_result_http"]="NOT VERIFIED: new Redis has no old Room participation"
                    result["rto_seconds"]=None;result["rpo_seconds"]=None;event("partial_backend_business_verified")
                    stop(app_process);app_process=None;stop(redis_process);redis_process=None
                    app_log.close();redis_log.close();event("owned_app_redis_stopped")
        finally:
            for process in (app_process,redis_process):
                try:stop(process)
                except BaseException as error:cleanup_errors.append(type(error).__name__)
            for handle in (app_log,redis_log,app_listener):
                if handle is not None:
                    try:handle.close()
                    except BaseException as error:cleanup_errors.append(type(error).__name__)
            if cleanup_errors:
                failure=failure or "CleanupIncomplete"
                event("owned_process_handle_cleanup_failed","FAIL")
            else:
                event("owned_process_handle_cleanup_verified")
      event("owned_private_temporary_material_removed")
    except BaseException as error:
        failure=type(error).__name__;event("partial_fixture_failure",failure)
    cleanup="verified: all owned temporary material removed" if temporary_name is not None and not Path(temporary_name).exists() else "incomplete: inspect private owned scratch"
    if cleanup_errors:cleanup="incomplete: owned process or handle cleanup failed"
    if cleanup.startswith("incomplete"):failure=failure or "CleanupIncomplete"
    finished=data.now();emit(args,started,finished,events,metrics,result,versions,cleanup,failure)
    print(json.dumps({"run_id":args.run_id,"partial_execution":"FAIL" if failure else "PASS","error_type":failure,
                      "full_t18_acceptance":"NOT RUN","service_rto":None,"service_rpo":None,"cleanup":cleanup}))
    if failure:raise SystemExit(1)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-checkout",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--run-id",required=True)
    parser.add_argument("--infra-sha",required=True)
    args=parser.parse_args()
    args.app_checkout=args.app_checkout.resolve();args.output=args.output.resolve()
    if not args.run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. " for c in args.run_id) or " " in args.run_id:
        parser.error("Run ID must be a safe single identifier")
    if len(args.infra_sha)!=40 or any(c not in "0123456789abcdef" for c in args.infra_sha):parser.error("Exact Infra SHA required")
    def interrupted(*_):raise InterruptedError("interrupted")
    signal.signal(signal.SIGTERM,interrupted)
    run(args)

if __name__=="__main__":main()
