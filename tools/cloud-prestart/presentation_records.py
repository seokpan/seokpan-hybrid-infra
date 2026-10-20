"""Start/stop owned read-only observers and preserve selected presentation evidence."""
import argparse,datetime as dt,hashlib,json,os,signal,stat,subprocess,sys,tarfile,time
from pathlib import Path
ROOT=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN=ROOT/'controller-run-bundle-K2Tx7HgB'
OUT=RUN/'presentation-final-20261020'
CID='2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'

def need(ok,code):
    if not ok:raise RuntimeError(code)
def read(path):
    need(path.is_file() and not path.is_symlink(),'REGULAR_OWNED_INPUT_REQUIRED')
    s=path.stat();need(s.st_uid==os.getuid() and s.st_size<=67108864 and stat.S_IMODE(s.st_mode)&0o077==0,'PRIVATE_BOUNDED_INPUT_REQUIRED')
    return json.loads(path.read_text())
def save(path,value):
    with open(path,'x') as f:json.dump(value,f,indent=2)
    path.chmod(0o600)
def stop(r):
    rows=[]
    for name,argv in r['observer_commands'].items():
        p=OUT/(name+'.process.json')
        if not p.exists():continue
        pid=read(p)['pid'];need(type(pid) is int and pid>1,'VALID_OWNED_OBSERVER_PID_REQUIRED')
        proc=Path('/proc')/str(pid)
        try:
            if not proc.exists():rows.append({'name':name,'status':'ALREADY_EXITED'});continue
            if proc.stat().st_uid!=os.getuid():rows.append({'name':name,'status':'PID_OWNER_CHANGED_NO_SIGNAL_SENT'});continue
            actual=(proc/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            if [x.decode() for x in actual]!=argv:rows.append({'name':name,'status':'PID_REUSED_NO_SIGNAL_SENT'});continue
            os.kill(pid,signal.SIGTERM);rows.append({'name':name,'status':'OWNED_READ_ONLY_OBSERVER_SIGTERM_SENT'})
        except ProcessLookupError:rows.append({'name':name,'status':'ALREADY_EXITED'})
    return rows
def summary(path):
    rows=[];bad=0
    if path.is_file():
        for line in path.read_text().splitlines():
            try:rows.append(json.loads(line))
            except Exception:bad+=1
    good=[x for x in rows if x.get('sample')]
    gaps=[(b['monotonic_ns']-a['monotonic_ns'])/1e9 for a,b in zip(good,good[1:])]
    return {'valid_samples':len(good),'failed_samples':len(rows)-len(good),'incomplete_lines':bad,
        'first_utc':good[0]['utc'] if good else None,'last_utc':good[-1]['utc'] if good else None,
        'maximum_sampled_total_connections':max((x['sample']['connections_total'] for x in good),default=None),
        'global_limits_observed':sorted({x['sample']['global_limit'] for x in good}),
        'actual_sample_intervals_seconds':gaps,'own_observer_connection_included':True,
        'continuous_peak_proven':False,'per_account_visibility_accepted':False,'hpa_accepted':False}
def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','stop','collect']);p.add_argument('--execute',action='store_true');a=p.parse_args()
    os.umask(0o077)
    try:
        need(a.execute,'EXPLICIT_OBSERVATION_ACTION_REQUIRED')
        r=read(OUT/'receipt.json');need(r['status']=='PASS_SELECTED_PRESENTATION_INPUTS_READY_NOT_TESTED' and r['cluster_id']==CID,'SAME_SUCCESSFUL_PREPARATION_REQUIRED')
        if a.action=='start':
            argv=r['observer_start_argv'];source=Path(argv[2])
            need(argv[:2]==[sys.executable,'-I'] and argv[3:]==['--execute','--start-observers']
                 and source.name=='finish_presentation_link.py' and source.is_relative_to(ROOT)
                 and hashlib.sha256(source.read_bytes().replace(b'\r\n',b'\n')).hexdigest()=='978d6b519333f48563bd9017153cda1d60ca414a0cc6036761dfc8ac7de62512',
                 'FIXED_PREPARATION_ENTRY_REQUIRED')
            return subprocess.run(argv,timeout=20).returncode
        stopped=stop(r)
        if a.action=='stop':print(json.dumps({'status':'READ_ONLY_OBSERVERS_STOP_REQUESTED','observers':stopped,'cluster_roll_cancelled':False}));return 0
        roll=RUN/'measurement-bindings/rolling-work';verified={'status':'ROLL_NOT_TRIGGERED'}
        if (roll/'trigger.STARTED.json').exists():
            argv=[sys.executable,'-I',str(RUN/'app-measurement/tools/presentation-measurement/rolling_control.py'),'verify','--config',str(RUN/'measurement-bindings/rolling-config.json'),'--work',str(roll),'--execute']
            result=subprocess.run(argv,capture_output=True,text=True,timeout=60)
            try:verified=json.loads(result.stdout.strip().splitlines()[-1])
            except Exception:verified={'status':'VERIFY_RESULT_UNAVAILABLE_RAW_WITHHELD'}
        totals=summary(OUT/'db-total-timeline.jsonl')
        result={'status':'COLLECTED_SELECTED_PRESENTATION_EVIDENCE_NOT_FINAL_ACCEPTANCE','cluster_id':CID,'observers':stopped,'rolling_verification':verified,'db_totals':totals,'load_repeated':False,'sync_submitted':False}
        stamp=str(time.time_ns());metadata=OUT/('collection-'+stamp+'.json');save(metadata,result)
        selected=[OUT/x for x in ('receipt.json','baseline.json','rolling.json','db-total-timeline.jsonl')]+[metadata]
        selected += [roll/x for x in ('before.json','trigger.STARTED.json','rolling-verified.json','deployment-pod-timeline.jsonl','pause.STARTED.json','resume.STARTED.json','recovery.STARTED.json')]
        archive=OUT/('controller-evidence-'+stamp+'.tar.gz')
        with tarfile.open(archive,'x:gz') as tar:
            for path in selected:
                if not path.exists():continue
                need(path.is_file() and not path.is_symlink() and path.stat().st_uid==os.getuid() and path.stat().st_size<=67108864,'BOUNDED_REGULAR_EVIDENCE_REQUIRED')
                tar.add(path,arcname=str(path.relative_to(RUN)),recursive=False)
        archive.chmod(0o600)
        result.update(archive=str(archive),archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),protected_credentials_in_archive=False)
        print(json.dumps(result,indent=2));return 0
    except Exception as e:
        print(json.dumps({'status':'STOP_PRESENTATION_RECORDS','reason':str(e) if type(e) is RuntimeError else type(e).__name__,'automatic_retry':False}));return 2
if __name__=='__main__':raise SystemExit(main())
