"""D 반환 폴더에 접근하지 않고 B 기록 보존과 삭제 Plan만 준비합니다."""
import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
import zipfile

ROOT=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN=ROOT/'controller-run-bundle-K2Tx7HgB'
BASE=RUN/'prestart-closeout'
RID='hpa-20261020-124957'
CID='2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'
ADOPTED_COLLECTOR_SHA='cea51056c14f27dc0f40489796b005a7059aaa0f339e3039b1967b324f7b962a'
TRANSPORT_SHA='79a0a85f194741de9782d030ac240e1bcd5828efb407473f6a73cf857dce3339'

def need(ok,code):
    if not ok:raise RuntimeError(code)

def raw(path,maximum=268435456):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
             and info.st_size<=maximum,'OWNED_B_RECORD_REQUIRED')
        return stream.read(maximum+1)

def read(path):return json.loads(raw(path))
def canon(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def save(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(canon(value)+b'\n')

def current():
    active=read(RUN/'hpa-active-time-copy.json');directory=Path(active['directory'])
    need(not directory.is_symlink() and directory.resolve().is_relative_to((RUN/RID).resolve()),
         'EXACT_B_RUN_DIRECTORY_REQUIRED')
    config=read(Path(active['config']));accepted=read(directory/'receipt.json')
    need(config['run_id']==RID and config['binding']['cluster_id']==CID
         and hashlib.sha256(canon(config)).hexdigest()==accepted['config_sha256'],
         'EXACT_CURRENT_B_BINDING_REQUIRED')
    return active,directory,config,accepted

def connect_export():
    target=BASE/'b_local_final_closeout.py';data=raw(Path(__file__).resolve(),1048576)
    if target.exists():need(raw(target)==data,'EXISTING_B_EXPORT_SOURCE_CHANGED')
    else:
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(data)
    transport=BASE/'metadata_transport.py';original=raw(transport,1048576)
    if b'b-local-final-summary-v1' in original:
        return
    need(hashlib.sha256(original.replace(b'\r\n',b'\n')).hexdigest()==TRANSPORT_SHA,
         'EXACT_ADOPTED_METADATA_TRANSPORT_REQUIRED')
    fingerprint=hashlib.sha256(data).hexdigest()
    hook="""    if command in {'b-local-final-summary-v1','b-local-final-archive-v1'}:
        helper=HERE/'b_local_final_closeout.py'
        need(sha(helper)=='FINGERPRINT','B_LOCAL_EXPORT_SOURCE_CHANGED')
        mode='--export-summary' if command=='b-local-final-summary-v1' else '--export-archive'
        os.execv(sys.executable,[sys.executable,'-I',str(helper),mode])
""".replace('FINGERPRINT',fingerprint)
    text=original.decode();needle='def route(command,settings):\n'
    need(text.count(needle)==1,'EXACT_METADATA_ROUTE_REQUIRED')
    updated=text.replace(needle,needle+hook).encode()
    backup=BASE/('metadata_transport.before-b-local-'+str(time.time_ns())+'.py')
    fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(original)
    fd,temp=tempfile.mkstemp(prefix='.b-local-export-',dir=BASE)
    with os.fdopen(fd,'wb') as stream:stream.write(updated);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,transport)

def collect():
    active,directory,config,accepted=current();out=directory/'b-local-final-closeout'
    if (out/'receipt.json').exists():
        connect_export();return read(out/'receipt.json')
    need(not out.exists(),'PARTIAL_B_LOCAL_RECORD_PRESERVED_NO_OVERWRITE')
    source=BASE/'finish_hpa_final_teardown.py'
    need(hashlib.sha256(raw(source)).hexdigest()==ADOPTED_COLLECTOR_SHA,
         'ADOPTED_B_COLLECTOR_REQUIRED')
    spec=importlib.util.spec_from_file_location('b_local_existing',source)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    stop={'status':'STOPPED_OWNED_B_READERS'}
    try:helper.stop_readers(directory)
    except (RuntimeError,PermissionError) as error:
        stop={'status':'UNVERIFIED_NO_FOREIGN_PROCESS_STOP',
              'reason':str(error) if type(error) is RuntimeError else type(error).__name__}
    data=raw(Path(active['work'])/'platform-db-metrics.jsonl')
    rows=[];invalid=0
    for line in data.splitlines():
        if not line.strip():continue
        try:row=json.loads(line)
        except ValueError:invalid+=1;continue
        need(row.get('binding')==config['binding'] and row.get('config_sha256')==accepted['config_sha256'],
             'FOREIGN_BOUND_B_SAMPLE_REJECTED')
        rows.append(row)
    out.mkdir(mode=0o700);public=out/'public';public.mkdir(mode=0o700)
    fd=os.open(out/'B-metrics.private.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(data)
    good=[r for r in rows if r.get('hpa') and r.get('db')]
    replicas=[r['hpa'].get('status',{}).get('currentReplicas') for r in good]
    replicas=[v for v in replicas if type(v) is int]
    times=[dt.datetime.fromisoformat(r['utc'].replace('Z','+00:00')).timestamp() for r in rows]
    summary={'status':'COLLECTED_B_LOCAL_OBSERVATIONS_D_EXTERNAL_PENDING','run_id':RID,
        'config_sha256':accepted['config_sha256'],'B_samples':len(rows),'invalid_lines_preserved':invalid,
        'B_first_utc':rows[0]['utc'] if rows else None,'B_last_utc':rows[-1]['utc'] if rows else None,
        'B_max_gap_seconds':max((b-a for a,b in zip(times,times[1:])),default=None),
        'max_replicas_observed':max(replicas,default=None),
        'last_replicas_observed':replicas[-1] if replicas else None,
        'max_connections_observed':max((r['db']['connections_total'] for r in good),default=None),
        'D_external_file_verification':'PENDING_SEPARATE_ZIP','B_reader_stop':stop,
        'restoration_verified':False,'restoration_disposition':'SKIPPED_FINAL_PROJECT_TEARDOWN',
        'scope':'ENTIRE_B_OBSERVATION_WINDOW_NOT_ONLY_D_LOAD_INTERVAL',
        'new_load_or_sql_or_sync':False,'project_deletion_started':False}
    save(public/'summary.json',summary)
    fd=os.open(public/'platform-db-metrics.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        for row in rows:stream.write(canon(helper.public_row(row))+b'\n')
    save(public/'SHA256SUMS.json',{p.name:hashlib.sha256(raw(p)).hexdigest() for p in public.iterdir()})
    archive=out/'B-public-evidence.zip'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as target:
        for p in public.iterdir():target.write(p,p.name)
    archive.chmod(0o600)
    result={'status':'PASS_B_LOCAL_EVIDENCE_PRESERVED_D_EXTERNAL_PENDING',
        'run_id':RID,'summary':summary,'public_archive':str(archive),
        'public_archive_sha256':hashlib.sha256(raw(archive)).hexdigest(),
        'D_return_path_accessed':False,'project_deletion_started':False}
    save(out/'receipt.json',result);connect_export();return result

def plan():
    active,directory,config,accepted=current()
    need(read(directory/'b-local-final-closeout/receipt.json')['status']==
         'PASS_B_LOCAL_EVIDENCE_PRESERVED_D_EXTERNAL_PENDING','B_LOCAL_EVIDENCE_REQUIRED')
    pointer=RUN/'b-final-delete-plan.json'
    if pointer.exists():
        out=Path(read(pointer)['directory']);print(json.dumps(read(out/'receipt.json'),indent=2))
        print('Private B delete Plan directory: '+str(out));return
    dispatch=read(RUN/'today-recreate-dispatch-20261020.json');lifecycle_config=read(Path(dispatch['config']))
    source=Path(dispatch['ops'])/'scripts/rosa-lifecycle-plan.py'
    spec=importlib.util.spec_from_file_location('b_local_delete_plan',source)
    lifecycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(lifecycle)
    out=Path(tempfile.mkdtemp(prefix='b-final-delete-plan-',dir=ROOT))
    result=lifecycle.prepare(SimpleNamespace(action='delete',cluster_id=CID,
        tf_root=lifecycle_config['create']['tf_root'],tf_data=lifecycle_config['tf_data'],
        var_file=lifecycle_config['create']['var_file']),out)
    result.pop('protected_files_require_encrypted_independent_copy',None)
    result.update(status='PASS_B_ROSA_DELETE_PLAN_ONLY_NOT_APPLIED',B_scope_only=True,
        A_resources_excluded=True,D_external_evidence_required_before_actual_delete=True,
        github_publication_required_before_actual_delete=True,apply_started=False)
    save(out/'receipt.json',result);save(pointer,{'directory':str(out)})
    print(json.dumps(result,indent=2));print('Private B delete Plan directory: '+str(out))

def export(archive):
    active,directory,config,accepted=current();out=directory/'b-local-final-closeout'
    result=read(out/'receipt.json');data=raw(out/'B-public-evidence.zip',25165824)
    need(hashlib.sha256(data).hexdigest()==result['public_archive_sha256'],'B_PUBLIC_ARCHIVE_CHANGED')
    if archive:result={'status':'PASS_B_LOCAL_PUBLIC_ARCHIVE_ONLY','sha256':result['public_archive_sha256'],
                       'base64':base64.b64encode(data).decode('ascii')}
    print(json.dumps(result))

if __name__=='__main__':
    os.umask(0o077);parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
    for flag in ('collect','prepare-plan','export-summary','export-archive'):group.add_argument('--'+flag,action='store_true')
    args=parser.parse_args()
    try:
        if args.collect:print(json.dumps(collect(),indent=2))
        elif args.prepare_plan:plan()
        else:export(args.export_archive)
    except Exception as error:
        print(json.dumps({'status':'STOP_B_LOCAL_FINAL_CLOSEOUT',
            'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'path':getattr(error,'filename',None),'automatic_retry':False,'project_deletion_started':False}))
        raise SystemExit(2)

