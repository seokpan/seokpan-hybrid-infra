"""Publish only HPA plans and guard state to the named local reader. No APIs."""
import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time


def need(ok,code):
    if not ok: raise RuntimeError(code)


def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def read(path,maximum=8388608):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_size<=maximum,
             'OWNED_BOUNDED_REGULAR_INPUT_REQUIRED')
        return json.load(f)


def acl(path,reader,directory=False):
    mode='r-x' if directory else 'r--'
    subprocess.run(['setfacl','-m','u:'+reader+':'+mode+',m::'+mode,str(path)],
                   check=True,capture_output=True,timeout=5)


def directory(path,reader):
    path.mkdir(mode=0o700,parents=False,exist_ok=True)
    need(not path.is_symlink() and path.stat().st_uid==os.getuid(),
         'OWNED_HANDOFF_DIRECTORY_REQUIRED')
    path.chmod(0o700)
    acl(path,reader,True)


def publish_file(path,data,reader,immutable=False):
    if path.exists():
        need(not path.is_symlink() and path.stat().st_uid==os.getuid(),
             'OWNED_PUBLICATION_TARGET_REQUIRED')
        if immutable:
            need(path.read_bytes()==data,'EXISTING_PUBLICATION_DIFFERS_NO_OVERWRITE')
            acl(path,reader)
            return
    fd,tmp=tempfile.mkstemp(prefix='.handoff-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data); f.flush(); os.fsync(f.fileno())
        acl(Path(tmp),reader)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def setup(c):
    need(pwd.getpwuid(os.getuid()).pw_name==c['owner'],'EXPECTED_OWNER_REQUIRED')
    need(c['reader']!=c['owner'],'DISTINCT_READ_ONLY_READER_REQUIRED')
    pwd.getpwnam(c['reader'])
    need(shutil.which('setfacl') is not None,'EXISTING_SETFACL_REQUIRED_NO_INSTALL')
    base=Path(c['handoff_root']); directory(base,c['reader'])
    code=base/('code-'+c['adopted_ref'][:12]); directory(code,c['reader'])
    source=Path(c['source_dir'])
    for name,expected in c['source_sha256'].items():
        need(re.fullmatch(r'[a-zA-Z0-9_.-]+',name),'EXACT_SOURCE_FILE_NAME_REQUIRED')
        p=source/name
        need(p.is_file() and not p.is_symlink() and p.stat().st_uid==os.getuid(),
             'OWNED_EXISTING_REVIEWED_SOURCE_REQUIRED')
        raw=p.read_bytes()
        need(hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()==expected,
             'ADOPTED_SOURCE_HASH_MISMATCH')
        publish_file(code/name,raw,c['reader'],True)
    helpers={}
    for name,row in c.get('helper_files',{}).items():
        need(re.fullmatch(r'[a-z_]+\.py',name),'EXACT_OPERATIONAL_HELPER_REQUIRED')
        path=Path(row['path']);need(path.is_file() and not path.is_symlink(),'OWNED_OPERATIONAL_HELPER_REQUIRED')
        raw=path.read_bytes();need(hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()==row['sha256'],
            'OPERATIONAL_HELPER_HASH_REQUIRED')
        publish_file(base/name,raw,c['reader'],True);helpers[name]=row['sha256']
    publish_file(base/'preparation.json',canonical({
        'status':'PREPARED_READ_ONLY_HANDOFF_NO_RUN_ACCEPTED','adopted_ref':c['adopted_ref'],
        'owner':c['owner'],'reader':c['reader'],'code_dir':str(code),
        'source_sha256':c['source_sha256'],'additional_operational_helpers':helpers,'cloud_calls':0}),c['reader'],True)
    return base,code


def public_contract(config):
    # Only approved fields are published. Passwords/tokens and private file contents
    # are never accepted as extensions to the read-only metadata contract.
    expected={'schema','run_id','actor','namespace','oc','personal_kubeconfig','api','origin',
        'binding','window','min_replicas','max_replicas','expected_images','normal_business_ref',
        'prior_comparison_finished_ref','capacity','db_probe_argv','generator_guard_argv',
        'db_local_paths','prometheus'}
    need(set(config)==expected,'EXACT_APPROVED_HPA_FIELDS_REQUIRED')
    def walk(x):
        if isinstance(x,dict):
            for k,v in x.items():
                need(not re.search(r'password|token|private_key|secret_value',k,re.I),
                     'CREDENTIAL_CONTENT_NOT_PUBLISHABLE')
                walk(v)
        elif isinstance(x,list):
            for v in x:walk(v)
    walk(config)
    # The same full config is needed for the approved guard fingerprint. It contains
    # file paths, not credentials. Parent directories of those files remain private.
    return config


def guard_row(row,fingerprint,binding,now):
    need(row['config_sha256']==fingerprint and row['binding']==binding,'GUARD_TARGET_CHANGED')
    need(row['status'] in ('PASS_HPA_GUARD','WAIT_HPA_METRICS'),'SOURCE_GUARD_STOPPED')
    age=now-dt.datetime.fromisoformat(row['utc'].replace('Z','+00:00')).timestamp()
    need(0<=age<=45,'SOURCE_GUARD_STALE')
    return row


def target_paths(c,run_id,code):
    need(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,45}',run_id),'RUN_ID_REQUIRED')
    target=Path(c['handoff_root'])/run_id
    argv=[c['python'],'-I',str(code/'control.py'),'guard','--config',str(target/'config.json'),
          '--work',str(target/'state'),'--execute']
    return target,argv


def validate_local(config,source):
    # Approved common.py validates inputs without executing the API client.
    sys.path.insert(0,str(source))
    spec=importlib.util.spec_from_file_location('handoff_hpa_common',source/'common.py')
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    need(not m.missing(config),'ACTUAL_HPA_INPUTS_REQUIRED')
    m.validate(config)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','bind','publish'])
    p.add_argument('--settings',type=Path,required=True)
    p.add_argument('--seconds',type=int,default=2700)
    a=p.parse_args(); os.umask(0o077)
    target=None; c=None
    try:
        c=read(a.settings); base,code=setup(c)
        if a.action=='prepare':
            print(json.dumps({'status':'PASS_READ_ONLY_HANDOFF_PREPARED_NO_RUN',
                'handoff_root':str(base),'code_dir':str(code),'reader':c['reader'],
                'personal_credentials_shared':False,'cloud_calls':0,'recreation_started':False}))
            return 0
        config_file=Path(c['source_config']); work=Path(c['source_work'])
        config=public_contract(read(config_file))
        target,argv=target_paths(c,config['run_id'],code)
        if a.action=='bind':
            need(not (work/'before.json').exists(),'EXISTING_HPA_RUN_MUST_NOT_BE_REBOUND')
            config['generator_guard_argv']=argv
            validate_local(config,Path(c['source_dir']))
            fd,tmp=tempfile.mkstemp(prefix='.bind-',dir=config_file.parent)
            with os.fdopen(fd,'wb') as f:f.write(canonical(config)+b'\n'); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,config_file)
            print(json.dumps({'status':'BOUND_LOCAL_GUARD_NO_CLOUD_CALLS','guard_argv':argv,
                              'shared_run_dir':str(target),'cloud_calls':0})); return 0
        need(1<=a.seconds<=3600,'BOUNDED_PUBLISH_DURATION_REQUIRED')
        validate_local(config,Path(c['source_dir']))
        need(config['generator_guard_argv']==argv,'BOUND_LOCAL_GUARD_ARGV_REQUIRED')
        fingerprint=hashlib.sha256(canonical(config)).hexdigest()
        plan=read(work/'plan.json')
        need(plan['run_id']==config['run_id'] and plan['binding']==config['binding']
             and plan['control_sha256']==fingerprint and plan['guard_argv']==argv,'SAME_PREPARED_PLAN_REQUIRED')
        directory(target,c['reader']); directory(target/'state',c['reader'])
        lock=os.open(target/'.publisher.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        need(not (target/'state/STOP.json').exists(),'CLOSED_FEED_REQUIRES_NEW_RUN')
        publish_file(target/'config.json',canonical(config)+b'\n',c['reader'],True)
        publish_file(target/'plan.json',canonical(plan)+b'\n',c['reader'],True)
        ready=work/'publisher-ready.json'
        need(not ready.exists(),'EXISTING_PUBLISHER_READY_MARKER_RECONCILE')
        fd=os.open(ready,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(canonical({'status':'READY_METADATA_FEED_NOT_HPA_READY',
            'config_sha256':fingerprint,'run_id':config['run_id'],'pid':os.getpid(),'shared_run_dir':str(target)}))
        deadline=min(time.monotonic()+a.seconds,time.monotonic()+max(0,
            dt.datetime.fromisoformat(config['window']['not_after'].replace('Z','+00:00')).timestamp()-time.time()))
        previous=None
        while time.monotonic()<deadline:
            need(hashlib.sha256(canonical(read(config_file))).hexdigest()==fingerprint,'RUN_CONFIG_CHANGED')
            need(not (work/'STOP.json').exists() and not (work/'restore.STARTED.json').exists(),
                 'B_STOP_OR_RESTORE_STARTED')
            if (work/'latest.json').exists():
                row=read(work/'latest.json')
                guard_row(row,fingerprint,config['binding'],time.time())
                raw=canonical(row)+b'\n'
                if raw!=previous:
                    publish_file(target/'state/latest.json',raw,c['reader']); previous=raw
            time.sleep(1)
        raise RuntimeError('PUBLISH_WINDOW_ENDED')
    except Exception as e:
        reason=str(e) if type(e) is RuntimeError else type(e).__name__
        if target is not None and (target/'state').is_dir() and c is not None:
            try:
                publish_file(target/'state/STOP.json',canonical({'status':'STOP_HANDOFF',
                    'reason':reason,'utc':dt.datetime.now(dt.timezone.utc).isoformat()}),c['reader'])
            except Exception:
                # A failed publisher cannot refresh latest.json; the guard expires.
                reason='PUBLICATION_FAILED_GUARD_WILL_EXPIRE'
        print(json.dumps({'status':'STOP_READ_ONLY_HANDOFF','reason':reason,
                          'cloud_calls':0,'recreation_started':False})); return 2


if __name__=='__main__': raise SystemExit(main())
