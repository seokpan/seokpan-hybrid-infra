"""Adopt the scoped observer CA fix and resume only the unfinished DB sample."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import time

OLD='bb15dc60e7b32bcf12bc46966b93dc0aeddb41f19e4f6f4f97ceab79d2e4f030'
NEW='d04d33713ab388469dc2c9567fe12acc3a30a1d217940efb700659a7a37cefaa'
CID='2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'

def need(ok,reason):
    if not ok:raise RuntimeError(reason)

def read(path):
    p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_size<8388608,'REGULAR_INPUT_REQUIRED')
    return json.loads(p.read_text())

def save(path,value):
    need(not path.is_symlink(),'REGULAR_OUTPUT_REQUIRED')
    with open(path,'w') as f:json.dump(value,f,indent=2)
    path.chmod(0o600)

def main():
    p=argparse.ArgumentParser();p.add_argument('--observer-source',type=Path,required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    need(a.execute,'EXPLICIT_OBSERVATION_EXECUTION_REQUIRED');os.umask(0o077)
    run=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62/controller-run-bundle-K2Tx7HgB'
    out=run/'current-monitoring-closeout-20261020';need(out.is_dir() and not out.is_symlink(),'EXISTING_PRIVATE_RESULT_DIRECTORY_REQUIRED')
    lock=open(out/'run.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    report=read(out/'receipt.json');need(report['cluster_id']==CID,'CURRENT_CLUSTER_REQUIRED')
    c=read(out/'db-observer.json');need(c['binding']['cluster_id']==CID,'CURRENT_DB_BINDING_REQUIRED')
    result={'status':'STOP_DB_CA_RESUME','cluster_id':CID,'app_sync_repeated':False,'monitoring_sync_repeated':False,
            'account_or_schema_changes':0,'new_pods':0,'automatic_retry':False,'maximum_seconds':90}
    try:
        dispatch=read(run/'current-fresh-app-run.json');inputs=read(Path(dispatch['work'])/'new-inputs.json')['result']['inputs']
        need(inputs['cluster_id']==CID and inputs['namespace_uid']==c['binding']['namespace_uid'],'SAME_ACCEPTED_INPUT_REQUIRED')
        c['database_ca_uid']=inputs['protected_uids']['configmap/backend-database-ca']
        need(a.observer_source.is_file() and not a.observer_source.is_symlink(),'REGULAR_REVIEW_SOURCE_REQUIRED')
        raw=a.observer_source.read_bytes().replace(b'\r\n',b'\n')
        need(hashlib.sha256(raw).hexdigest()==NEW,'NEW_OBSERVER_HASH_REQUIRED')
        target=run/'prestart-closeout/pod_db_observer.py';need(target.is_file() and not target.is_symlink(),'EXISTING_OBSERVER_REQUIRED')
        info=target.stat();need(info.st_uid==os.getuid() and stat.S_ISREG(info.st_mode),'OWNED_OBSERVER_REQUIRED')
        original=target.read_bytes();current=hashlib.sha256(original.replace(b'\r\n',b'\n')).hexdigest()
        need(current in (OLD,NEW),'UNEXPECTED_OBSERVER_PRESERVED')
        if current==OLD:
            backup=out/'pod_db_observer.before-ca-fix.py'
            if backup.exists():need(backup.read_bytes()==original,'EXISTING_BACKUP_MISMATCH')
            else:
                fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'wb') as f:f.write(original)
            target.write_bytes(raw);target.chmod(0o600)
        save(out/'db-observer.json',c)
        print(json.dumps({'phase':'OBSERVE_DB_WITH_CURRENT_BACKEND_CA','old_data_pod_path_reused':False}),flush=True)
        spec=importlib.util.spec_from_file_location('current_db_observer',target)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        end=time.monotonic()+90
        def runner(*args,**kwargs):
            left=end-time.monotonic();need(left>0,'DB_SAMPLE_TIME_BOUND_REACHED')
            kwargs['timeout']=min(kwargs.get('timeout',18),left)
            return subprocess.run(*args,**kwargs)
        if (out/'db-sample.json').exists():
            sample=read(out/'db-sample.json');need(sample.get('status')=='PASS_CURRENT_DB_SAMPLE' and sample['binding']==c['binding'],'SAME_ACCEPTED_SAMPLE_REQUIRED')
            result['sample_reused']=True
        else:
            sample=m.sample(c,runner=runner);save(out/'db-sample.json',sample)
            result['sample_reused']=False
        report['db_observer']=sample;report['remaining']=[x for x in report['remaining'] if x!='DB_OBSERVER_ACTUAL_ACCEPTANCE']
        report['status']='PASS_B_RUNTIME_CLOSEOUT_NOT_LOAD_TEST' if not report['remaining'] else 'PARTIAL_B_RUNTIME_CLOSEOUT'
        report['window_extended']=True;report['extension_scope']='USER_AUTHORIZED_DB_ONLY_MAXIMUM_90_SECONDS'
        save(out/'receipt.json',report);save(out/'D-runtime-handoff.json',report)
        result.update(status=report['status'],sample=sample,remaining=report['remaining'],load_test_started=False)
    except Exception as e:
        result['reason']=str(e) if type(e) is RuntimeError else type(e).__name__
    path=out/('db-ca-resume-'+str(time.time_ns())+'.json');save(path,result)
    print(json.dumps(result,indent=2));print('Private DB observation result: '+str(path))
    return 0 if result['status'].startswith('PASS_') else 2

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as e:
        print(json.dumps({'status':'STOP_DB_CA_RESUME_SETUP','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
                          'raw_logs_printed':False,'automatic_retry':False}));raise SystemExit(2)
