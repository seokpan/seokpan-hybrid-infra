"""종료 기록의 정확한 권한 오류만 보완하고 기존 수집과 오프라인 분석을 잇습니다."""
import collections
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

ROOT=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN=ROOT/'controller-run-bundle-K2Tx7HgB'
BASE=RUN/'prestart-closeout'
RID='hpa-20261020-124957'
RETURN=Path('/var/tmp/seokpan-hpa-handoff/return/cyj/d-start-window')/RID
SOURCE_SHA='cea51056c14f27dc0f40489796b005a7059aaa0f339e3039b1967b324f7b962a'
CONTEXT={}

REPAIR=r'''
import hashlib,json,os,pwd,stat,subprocess
from pathlib import Path
if pwd.getpwuid(os.getuid()).pw_name!='cyj':
    raise SystemExit('STOP_USE_RESULT_OWNER_CYJ')
base=Path('/var/tmp/seokpan-hpa-handoff/return/cyj')
root=base/'d-start-window/hpa-20261020-124957'
files=['SHA256SUMS.json','receipt.json','counts.json','requests.jsonl','events.jsonl','generator.jsonl']
for p in [Path('/var'),Path('/var/tmp'),Path('/var/tmp/seokpan-hpa-handoff'),
          Path('/var/tmp/seokpan-hpa-handoff/return'),base,base/'d-start-window',root]:
    s=p.lstat()
    if not stat.S_ISDIR(s.st_mode) or p.is_symlink():
        raise RuntimeError('EXACT_REGULAR_RETURN_DIRECTORIES_REQUIRED')
for name in files:
    p=root/name;s=p.lstat()
    if not stat.S_ISREG(s.st_mode) or p.is_symlink() or s.st_uid!=os.getuid() or s.st_size>268435456:
        raise RuntimeError('EXACT_OWNED_RETURN_FILES_REQUIRED')
manifest=json.loads((root/'SHA256SUMS.json').read_text())
if set(manifest)!=(set(files)-{'SHA256SUMS.json'}):
    raise RuntimeError('EXACT_SIX_RETURNED_FILES_REQUIRED')
for name,digest in manifest.items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('RETURNED_FILE_HASH_CHANGED')
changed=[]
targets=[(base.parent,'--x'),(base,'--x'),(base/'d-start-window','--x'),(root,'r-x')]
targets.extend((root/name,'r--') for name in files)
for path,wanted in targets:
    if path.stat().st_uid!=os.getuid():
        continue
    acl=subprocess.run(['getfacl','-cp','--',str(path)],capture_output=True,text=True,check=True,timeout=5).stdout
    entry=next((line.split('#',1)[0].strip() for line in acl.splitlines() if line.startswith('user:jth:')),None)
    mask=next((line.split(':',1)[1].strip() for line in acl.splitlines() if line.startswith('mask:')), 'rwx')
    current=entry.split(':',2)[2] if entry else '---'
    needed={x for x in wanted if x!='-'}
    if needed<=set(current) and needed<=set(mask):
        continue
    combined=''.join(x if x in set(current)|needed else '-' for x in 'rwx')
    subprocess.run(['setfacl','-m','u:jth:'+combined,'--',str(path)],check=True,capture_output=True,timeout=5)
    changed.append(str(path.relative_to(base)))
print(json.dumps({'status':'PASS_EXACT_D_RETURN_READ_ACL_REPAIRED',
    'changed':changed,'files_changed':0,'other_named_acl_entries_changed':False,
    'cloud_calls':0,'sql_calls':0,'load_repeated':False}))
'''

def need(ok,code):
    if not ok:
        raise RuntimeError(code)

def analyse(rows,receipt):
    groups={}
    recent=collections.deque(maxlen=100)
    first_crossing=None
    for index,row in enumerate(rows,1):
        need(row.get('method')=='GET' and row.get('path') in
             ('/api/v1/rooms','/api/v1/rankings?offset=0&limit=20'), 'EXACT_GUEST_READ_REQUESTS_REQUIRED')
        phase=row['phase']
        g=groups.setdefault(phase,{'phase':phase,'N':0,'http_status':{},'http_4xx':0,
            'http_5xx':0,'response_missing':0,'non_200':0,'semantic_failure_total':0,
            'http_200_content_failure':0,'elapsed':[]})
        status=row.get('status');g['N']+=1
        key=str(status) if type(status) is int else 'NO_RESPONSE'
        g['http_status'][key]=g['http_status'].get(key,0)+1
        g['http_4xx']+=int(type(status) is int and 400<=status<500)
        g['http_5xx']+=int(type(status) is int and 500<=status<600)
        g['response_missing']+=int(type(status) is not int)
        g['non_200']+=int(status!=200)
        g['semantic_failure_total']+=int(row.get('semantic_ok') is not True)
        g['http_200_content_failure']+=int(status==200 and row.get('semantic_ok') is not True)
        value=row.get('elapsed_ms')
        if type(value) in (int,float) and math.isfinite(value) and value>=0:g['elapsed'].append(value)
        recent.append(row)
        selected=[r for r in recent if r['phase']==phase]
        failures=sum(r.get('semantic_ok') is not True for r in selected)
        if first_crossing is None and len(selected)>=20 and failures/len(selected)>.05:
            first_crossing={'phase':phase,'global_record_index':index,'N':len(selected),
                'failures':failures,'fraction':failures/len(selected),'utc':row.get('utc'),
                'interpretation':'RECONSTRUCTED_CROSSING_FROM_RECORD_ORDER_NOT_CAUSAL_TRACE'}
    phases=[]
    for phase,g in sorted(groups.items()):
        elapsed=sorted(g.pop('elapsed'))
        for q,name in ((.50,'p50_ms'),(.95,'p95_ms'),(.99,'p99_ms')):
            g[name]=elapsed[max(0,math.ceil(len(elapsed)*q)-1)] if elapsed else None
        g['http_5xx_fraction']=g['http_5xx']/g['N']
        phases.append(g)
    return {'status':'COLLECTED_OFFLINE_REQUEST_ANALYSIS','run_id':RID,
        'D_status':receipt['status'],'stop_reason':receipt.get('reason'),
        'D_started_utc':receipt.get('started_utc'),'D_finished_utc':receipt.get('finished_utc'),
        'request_rows':len(rows),'D_reported_requests':receipt.get('requests'),
        'row_count_matches_D_receipt':len(rows)==receipt.get('requests'),
        'phase_requests':phases,'first_reconstructed_error_budget_crossing':first_crossing,
        'error_budget':'More than 5 percent semantic failures among same-phase rows in last 100 global records; at least 20 rows',
        'aws_cost_caused_generator_stop':False,'maximum_capacity_proven':False,
        'new_requests_sent':0}

def resume():
    import importlib.util
    need(pwd.getpwuid(os.getuid()).pw_name=='jth','USE_CONTROLLER_JTH')
    source=BASE/'finish_hpa_final_teardown.py'
    need(not source.is_symlink() and hashlib.sha256(source.read_bytes()).hexdigest()==SOURCE_SHA,
         'EXISTING_ADOPTED_FINAL_COLLECTOR_REQUIRED')
    spec=importlib.util.spec_from_file_location('adopted_final_collect',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original_raw=module.raw
    def traced(path,*args,**kwargs):
        CONTEXT.update(stage='READ_EXISTING_EVIDENCE',path=str(path))
        return original_raw(path,*args,**kwargs)
    module.raw=traced
    stop=module.stop_readers
    def stop_traced(directory):
        CONTEXT.update(stage='STOP_OWNED_LOCAL_READERS',path=str(directory))
        return stop(directory)
    module.stop_readers=stop_traced
    try:
        result=module.finish()
    except PermissionError as error:
        path=Path(error.filename or CONTEXT.get('path',''))
        scoped=(path==RETURN or RETURN in path.parents or path in
                [RETURN.parent,RETURN.parent.parent])
        need(scoped and CONTEXT.get('stage')=='READ_EXISTING_EVIDENCE',
             'PERMISSION_FAILURE_OUTSIDE_D_RETURN_NO_AUTOMATIC_CHANGE')
        print(json.dumps({'phase':'EXACT_D_RETURN_PERMISSION_DENIED','path':str(path)}),flush=True)
        repair=subprocess.run(['sudo','-n','-u','cyj','/usr/bin/python3.9','-I','-'],
            input=REPAIR.encode(),capture_output=True,timeout=60)
        if repair.returncode:
            print(json.dumps({'status':'STOP_RESULT_OWNER_READ_ACL_REQUIRED',
                'sudo_exit_code':repair.returncode,'owner':'cyj',
                'next':'RUN_SAME_HELPER_AS_CYJ_THEN_RESUME_JTH','automatic_retry':False}))
            raise SystemExit(3)
        print(repair.stdout.decode(),end='',flush=True)
        result=module.finish()
    print(json.dumps(result,indent=2),flush=True)
    need(result['status']=='PASS_FINAL_TEARDOWN_EVIDENCE_PRESERVED_NOT_TEST_VERDICT',
         'FINAL_TEARDOWN_RECEIPT_REQUIRED')
    active=module.read(RUN/'hpa-active-time-copy.json')
    accepted=module.read(Path(active['directory'])/'receipt.json')
    returned=Path(accepted['D_return_root'])/RID
    evidence=returned/'requests.jsonl'
    analysis_file=Path(active['directory'])/'final-closeout/request-analysis.json'
    if analysis_file.exists():
        analysis=module.read(analysis_file)
        need(analysis['requests_sha256']==hashlib.sha256(original_raw(evidence)).hexdigest(),
             'EXISTING_REQUEST_ANALYSIS_SOURCE_CHANGED')
    else:
        data=original_raw(evidence)
        rows=[json.loads(line) for line in data.splitlines() if line.strip()]
        analysis=analyse(rows,module.read(returned/'receipt.json'))
        analysis.update(requests_sha256=hashlib.sha256(data).hexdigest(),
            public_archive_sha256=result['public_archive_sha256'])
        module.save(analysis_file,analysis)
    print(json.dumps(analysis,indent=2),flush=True)
    pointer=RUN/'b-final-delete-plan.json'
    if pointer.exists():
        directory=Path(module.read(pointer)['directory'])
        print(json.dumps(module.read(directory/'receipt.json'),indent=2))
        print('Private B delete Plan directory: '+str(directory))
    else:
        process=subprocess.run(['bash',str(BASE/'run_b_final_delete_plan.sh'),'--prepare-plan'])
        need(process.returncode==0,'B_DELETE_PLAN_PENDING_NO_APPLY')
    print('STOP_BEFORE_ACTUAL_DELETION_USER_CONFIRMATION_REQUIRED')

if __name__=='__main__':
    os.umask(0o077)
    try:
        if pwd.getpwuid(os.getuid()).pw_name=='cyj':
            exec(compile(REPAIR,'scoped-return-acl','exec'))
        else:
            resume()
    except Exception as error:
        print(json.dumps({'status':'STOP_FINAL_HANDOFF_RESUME',
            'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'stage':CONTEXT.get('stage'),'path':getattr(error,'filename',None) or CONTEXT.get('path'),
            'automatic_retry':False,'project_deletion_started':False}))
        raise SystemExit(2)

