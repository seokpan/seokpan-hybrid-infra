"""동일 D 결과의 외부 인수 사본과 종료된 B 기록으로 최종 폐기 증거를 만듭니다."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import zipfile

RUN = Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62/controller-run-bundle-K2Tx7HgB'
EXPECTED = None
ALLOWED = {'receipt.json','counts.json','requests.jsonl','events.jsonl','generator.jsonl'}

def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)

def raw(path, maximum=268435456):
    if Path(path).is_symlink():
        raise OSError('SYMLINK_EVIDENCE_REJECTED')
    fd = os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_size<=maximum,'BOUNDED_REGULAR_EVIDENCE_REQUIRED')
        return stream.read(maximum+1)

def read(path):
    return json.loads(raw(path))

def canon(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def save(path, value):
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(canon(value)+b'\n')

def module(path):
    spec = importlib.util.spec_from_file_location('existing_hpa_finalize',path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj

def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()

def command(argv, out, stage):
    done = out/(stage+'.complete.json')
    if done.exists():
        return read(done)
    marker = out/(stage+'.attempted.json')
    need(not marker.exists(),'PREVIOUS_'+stage.upper()+'_OUTCOME_REQUIRES_RECONCILIATION')
    save(marker,{'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'automatic_retry':False})
    p = subprocess.run(argv,capture_output=True,timeout=180)
    try:
        result = json.loads(p.stdout.splitlines()[-1])
    except Exception:
        result = {'status':'STOP_COMMAND_RESULT_UNCLASSIFIED'}
    save(out/(stage+'.private.json'),{'exit_code':p.returncode,'result':result})
    need(p.returncode==0,stage.upper()+'_FAILED_PRIVATE_RESULT_NO_RETRY')
    save(done,result)
    return result

def stop_readers(directory):
    pending = []
    for name in ('observer','publisher'):
        row = read(directory/(name+'.process.json'))
        proc = Path('/proc')/str(row['pid'])
        def alive():
            try:
                need(proc.stat().st_uid == os.getuid(),'OWNED_READER_REQUIRED')
                value = (proc/'cmdline').read_bytes()
            except FileNotFoundError:
                return False
            if not value:
                return False
            need([s.decode() for s in value.rstrip(b'\0').split(b'\0')] == row['argv'],
                 'EXACT_READER_ARGUMENTS_REQUIRED')
            need(os.getpgid(row['pid']) == row['pid'],'DETACHED_READER_REQUIRED')
            return True
        end = time.monotonic()+20
        while alive() and time.monotonic()<end:
            time.sleep(.5)
        if alive():
            os.killpg(row['pid'],signal.SIGTERM)
            end = time.monotonic()+5
            while alive() and time.monotonic()<end:
                time.sleep(.1)
        if alive():
            pending.append(name)
    need(not pending,'READER_EXIT_PENDING_PRESERVE_STATE')

def public_row(row):
    hpa = row.get('hpa',{})
    db = row.get('db',{})
    reason = row.get('reason')
    reason = reason if reason is None or re.fullmatch(r'[A-Z0-9_]{1,180}',str(reason)) else 'DETAIL_WITHHELD'
    return {'utc':row['utc'],'status':row['status'],'reason':reason,
        'hpa':{'min':hpa.get('spec',{}).get('minReplicas'),
               'max':hpa.get('spec',{}).get('maxReplicas'),
               'current':hpa.get('status',{}).get('currentReplicas'),
               'desired':hpa.get('status',{}).get('desiredReplicas')},
        'db':{k:db.get(k) for k in ('tls','global_limit','global_max_user_connections',
                                    'connections_total','per_account')},
        'deployments':{name:{'replicas':v.get('replicas'),
             'ready':v.get('status',{}).get('readyReplicas',0)}
             for name,v in row.get('deployments',{}).items()},
        'pod_count':len(row.get('pods',[])),
        'terminating_pod_count':sum(bool(p.get('deleting')) for p in row.get('pods',[]))}

def finish(returned_override=None):
    import pwd
    global EXPECTED
    need(pwd.getpwuid(os.getuid()).pw_name == 'jth','USE_CONTROLLER_JTH')
    d = read(RUN/'hpa-active-time-copy.json')
    directory, work = Path(d['directory']),Path(d['work'])
    need(directory.resolve().is_relative_to((RUN/'hpa-20261020-124957').resolve()),
         'EXACT_B_RUN_DIRECTORY_REQUIRED')
    c = read(Path(d['config']))
    accepted = read(directory/'receipt.json')
    need(accepted['status'] in ('PASS_TIME_COPY_BOUND_SAME_HPA_D_START_GO',
         'PASS_D_START_WINDOW_BOUND_SAME_HPA_GO') and
         accepted['hpa_uid']=='657111ed-f39c-4bdc-a86d-15147d41f41d',
         'ACCEPTED_SAME_HPA_TIME_COPY_REQUIRED')
    EXPECTED = accepted['config_sha256']
    need(hashlib.sha256(canon(c)).hexdigest()==EXPECTED,'ACCEPTED_TIME_COPY_REQUIRED')
    out = directory/'final-closeout'
    out.mkdir(mode=0o700,exist_ok=True)
    need(not out.is_symlink() and out.stat().st_uid==os.getuid(),'OWNED_CLOSEOUT_DIRECTORY_REQUIRED')
    if (out/'receipt.json').exists():
        return read(out/'receipt.json')
    returned = Path(accepted['D_return_root'])/c['run_id'] if returned_override is None else Path(returned_override)
    if returned_override is not None:
        need(returned.resolve()==(directory/'external-d-results').resolve(),
             'EXACT_EXTERNAL_RESULT_DIRECTORY_REQUIRED')
    manifest = read(returned/'SHA256SUMS.json')
    need(set(manifest)<=ALLOWED and {'receipt.json','counts.json'}<=set(manifest),
         'EXACT_D_RESULT_MANIFEST_REQUIRED')
    stopped = read(returned/'receipt.json')
    need(stopped['run_id']==c['run_id'] and stopped['binding']==c['binding']
         and stopped['control_sha256']==EXPECTED
         and stopped['status'] in ('COMPLETED_HPA_LOAD','ABORTED_HPA_LOAD')
         and stopped['in_flight_at_end']==0,'MATCHING_STOPPED_D_RECEIPT_REQUIRED')
    for name, expected in manifest.items():
        value = raw(returned/name)
        need(hashlib.sha256(value).hexdigest()==expected,'D_EVIDENCE_HASH_MISMATCH')
    source = Path(d['observer_argv'][2]).parent
    args = ['--config',d['config'],'--work',str(work)]
    control = [sys.executable,'-I',str(source/'control.py')]
    if returned_override is None:
        stop_readers(directory)
    else:
        local = read(directory/'b-local-final-closeout/receipt.json')
        need(local['summary']['B_reader_stop']['status']=='STOPPED_OWNED_B_READERS',
             'B_READERS_ALREADY_STOPPED_REQUIRED')
        for name in ('observer','publisher'):
            marker = read(directory/(name+'.process.json'))
            proc = Path('/proc')/str(marker['pid'])/'cmdline'
            try:
                argv = [x.decode() for x in proc.read_bytes().rstrip(b'\0').split(b'\0')]
            except FileNotFoundError:
                continue
            need(argv!=marker['argv'],'B_READER_RESTARTED_REQUIRES_RECONCILIATION')
    metrics = work/'platform-db-metrics.jsonl'
    rows = [json.loads(line) for line in raw(metrics).splitlines() if line.strip()] if metrics.exists() else []
    need(all(r['binding']==c['binding'] and r['config_sha256']==EXPECTED for r in rows),
         'SAME_TIME_COPY_OBSERVATIONS_REQUIRED')
    counts = read(returned/'counts.json')
    need(counts['binding']==c['binding'] and counts['run_id']==c['run_id']
         and counts['control_sha256']==EXPECTED,'COUNTS_FROM_OTHER_RUN')
    checker = module(source/'results.py')
    phase_checks = checker.nonzero_phase_acceptance(counts['phases'])
    good = [r for r in rows if r.get('hpa') and r.get('db')]
    replicas = [r['hpa'].get('status',{}).get('currentReplicas') for r in good]
    replicas = [v for v in replicas if type(v) is int]
    before = read(work/'before.json')
    baseline = before['deployments']['backend']['spec']['replicas']
    result = {'status':'OBSERVED_HPA_METRICS_FINAL_TEARDOWN_NO_RESTORATION_VERDICT',
        'binding':c['binding'],'baseline_replicas':baseline,
        'max_replicas_observed':max(replicas,default=None),
        'scale_out_observed':bool(replicas) and max(replicas)>baseline,
        'last_observed_min_replica':bool(replicas) and replicas[-1]==c['min_replicas'],
        'all_nonzero_phases_http_5xx_below_1_percent':all(x['status']=='PASS' for x in phase_checks),
        'phase_acceptance':phase_checks,
        'max_connections_observed':max((r['db']['connections_total'] for r in good),default=None),
        'min_global_headroom_observed':min((r['db']['global_limit']-r['db']['connections_total'] for r in good),default=None),
        'failed_or_waiting_observer_samples':sum(r['status']!='PASS_HPA_GUARD' for r in rows),
        'clock_domain_correlation':'UTC labels only; no cross-host scale latency computed',
        'restoration_verified':False,'restoration_disposition':'SKIPPED_FINAL_PROJECT_TEARDOWN',
        'guest_cleanup_unconfirmed':stopped.get('guest_cleanup_unconfirmed'),
        'scope':'Guest lobby/ranking reads; not game vote/WSS capacity; original configuration restoration not tested'}
    result_path = directory/'hpa-final-teardown-result.json'
    if not result_path.exists():
        save(result_path,result)
    else:
        need(read(result_path)==result,'EXISTING_FINAL_RESULT_DIFFERS_NO_OVERWRITE')
    times = [timestamp(r['utc']) for r in rows]
    gaps = [b-a for a,b in zip(times,times[1:])]
    public = out/'public'
    public.mkdir(mode=0o700,exist_ok=True)
    summary = {k:result.get(k) for k in ('status','baseline_replicas','max_replicas_observed',
        'scale_out_observed','last_observed_min_replica','all_nonzero_phases_http_5xx_below_1_percent',
        'phase_acceptance','max_connections_observed','min_global_headroom_observed',
        'failed_or_waiting_observer_samples','clock_domain_correlation','restoration_verified',
        'guest_cleanup_unconfirmed','scope','restoration_disposition')}
    summary.update(run_id=c['run_id'],config_sha256=EXPECTED,D_status=stopped['status'],
        D_started_utc=stopped['started_utc'],D_finished_utc=stopped['finished_utc'],
        B_samples=len(rows),B_max_gap_seconds=max(gaps,default=None),
        B_first_utc=rows[0]['utc'] if rows else None,B_last_utc=rows[-1]['utc'] if rows else None,
        observations_scope='ENTIRE_B_OBSERVATION_WINDOW_NOT_ONLY_NONZERO_VU_PHASES',
        original_first_attempt_guest_cleanup='NOT_CONFIRMED',
        project_deletion_started=False)
    if not (public/'summary.json').exists():
        save(public/'summary.json',summary)
    if not (public/'platform-db-metrics.jsonl').exists():
        fd = os.open(public/'platform-db-metrics.jsonl',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream:
            for row in rows:
                stream.write(canon(public_row(row))+b'\n')
    # 공개 사본에서는 플랫폼 식별자와 응답 본문을 포함하지 않습니다.
    receipt_public = {k:stopped.get(k) for k in ('status','run_id','control_sha256',
        'started_utc','finished_utc','workload','phases','requests','in_flight_at_end',
        'guest_issue_attempts','guest_issue_without_confirmed_cleanup_handle',
        'guest_sessions_created','guest_sessions_cleanup_confirmed',
        'guest_cleanup_unconfirmed','created_member_rows','created_rooms',
        'no_game_or_wss_capacity_claim','control_failure_observed')}
    reason = stopped.get('reason')
    receipt_public['reason'] = reason if reason is None or re.fullmatch(
        r'[A-Z0-9_]{1,180}',str(reason)) else 'DETAIL_WITHHELD'
    if not (public/'D-receipt.json').exists():
        save(public/'D-receipt.json',receipt_public)
    counts = read(returned/'counts.json')
    if not (public/'D-counts.json').exists():
        save(public/'D-counts.json',{k:counts.get(k) for k in ('routes','phases','run_id','control_sha256')})
    allowed_keys = {
        'requests.jsonl':{'phase','vu','attempt','method','path','status','semantic_ok',
                         'elapsed_ms','started_ms','utc','retry_of'},
        'events.jsonl':{'phase','vus','seconds','started_utc','started_ms','finished_utc',
                       'actual_seconds','event'},
        'generator.jsonl':{'utc','elapsed_ms','cpu','memory','event_loop_p99_ms'}}
    for name, keys in allowed_keys.items():
        if name not in manifest or (public/name).exists():
            continue
        value = raw(returned/name)
        for line in value.splitlines():
            item = json.loads(line)
            need(set(item)<=keys,'UNEXPECTED_D_PUBLIC_FIELDS')
            if name=='requests.jsonl':
                need(item.get('method')=='GET' and item.get('path') in
                     ('/api/v1/rooms','/api/v1/rankings?offset=0&limit=20'),
                     'SCOPED_PUBLIC_HTTP_PATH_REQUIRED')
        fd = os.open(public/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(value)
    hashes = {p.name:hashlib.sha256(raw(p)).hexdigest() for p in public.iterdir() if p.is_file()}
    if not (public/'SHA256SUMS.json').exists():
        save(public/'SHA256SUMS.json',hashes)
    archive = out/'public-evidence.zip'
    if not archive.exists():
        with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
            for p in public.iterdir():
                z.write(p,p.name)
        archive.chmod(0o600)
    private = out/'private-evidence.tar.gz'
    if not private.exists():
        with tarfile.open(private,'x:gz') as t:
            for name in ('before.json','plan.json','hpa.json','hpa-created.json','restored.json',
                         'platform-db-metrics.jsonl','STOP.json'):
                p = work/name
                if p.is_file():
                    t.add(p,arcname='B/'+name,recursive=False)
            for name in sorted(set(manifest)|{'SHA256SUMS.json'}):
                t.add(returned/name,arcname='D/'+name,recursive=False)
            for p in (directory/'config.json',directory/'time-binding-bridge.json',result_path):
                t.add(p,arcname='binding/'+p.name,recursive=False)
        private.chmod(0o600)
    final = {'status':'PASS_FINAL_TEARDOWN_EVIDENCE_PRESERVED_NOT_TEST_VERDICT',
        'run_id':c['run_id'],'test_verdict':result['status'],'D_status':stopped['status'],
        'max_replicas_observed':result['max_replicas_observed'],
        'max_connections_observed':result['max_connections_observed'],
        'B_samples':len(rows),'B_max_gap_seconds':max(gaps,default=None),
        'restoration_verified':False,'final_teardown_selected':True,
        'generator_stopped':True,'B_readers_stopped':True,
        'restoration_disposition':'SKIPPED_FINAL_PROJECT_TEARDOWN','public_summary':str(public/'summary.json'),
        'public_archive':str(archive),'public_archive_sha256':hashlib.sha256(raw(archive)).hexdigest(),
        'private_archive':str(private),'private_archive_sha256':hashlib.sha256(raw(private)).hexdigest(),
        'github_evidence_published':False,'project_deletion_started':False}
    save(out/'receipt.json',final)
    return final

if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--finish-run',action='store_true')
    parser.add_argument('--final-teardown-no-restore',action='store_true')
    args = parser.parse_args()
    try:
        need(args.finish_run and args.final_teardown_no_restore,'EXPLICIT_FINAL_TEARDOWN_FLAG_REQUIRED')
        print(json.dumps(finish(),indent=2))
    except Exception as error:
        print(json.dumps({'status':'STOP_POST_TEST_CLOSEOUT',
            'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'automatic_retry':False,'project_deletion_started':False,
            'next':'PRESERVE_CONTROL_AND_RETURNED_EVIDENCE_NO_REPEATED_WRITES'}))
        raise SystemExit(2)

