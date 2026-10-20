"""D local generator and read-only return to B. No B kubeconfig/API operations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile

def need(ok,code):
    if not ok:raise RuntimeError(code)
def read(path):
    p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_size<1048576,'BOUNDED_REGULAR_PLAN_REQUIRED')
    return json.loads(p.read_text())
def acl(path,reader,directory=False):
    mode='r-x' if directory else 'r--'
    subprocess.run(['setfacl','-m','u:'+reader+':'+mode+',m::'+mode,str(path)],check=True,capture_output=True,timeout=5)
def directory(path,reader):
    path.mkdir(mode=0o700,exist_ok=True)
    need(not path.is_symlink() and path.stat().st_uid==os.getuid(),'OWNED_RESULT_DIRECTORY_REQUIRED')
    path.chmod(0o700);acl(path,reader,True)

def return_results(plan,work,root,reader):
    receipt=read(work/'receipt.json')
    need(receipt['run_id']==plan['run_id'] and receipt['binding']==plan['binding']
        and receipt['control_sha256']==plan['control_sha256'] and receipt['in_flight_at_end']==0
        and receipt['status'] in {'COMPLETED_HPA_LOAD','ABORTED_HPA_LOAD'},'SAME_STOPPED_GENERATOR_RECEIPT_REQUIRED')
    hashes=read(work/'SHA256SUMS.json')
    need(set(hashes)<= {'receipt.json','counts.json','requests.jsonl','events.jsonl','generator.jsonl'}
        and {'receipt.json','counts.json'}<=set(hashes),'EXACT_GENERATOR_RESULT_FILES_REQUIRED')
    directory(root,reader);target=root/plan['run_id'];directory(target,reader)
    for name,expected in hashes.items():
        source=work/name;need(source.is_file() and not source.is_symlink() and source.stat().st_uid==os.getuid(),
            'OWNED_GENERATOR_RESULT_REQUIRED')
        fd,tmp=tempfile.mkstemp(prefix='.result-',dir=target);digest=hashlib.sha256();size=0
        try:
            with os.fdopen(fd,'wb') as dst,source.open('rb') as src:
                while True:
                    chunk=src.read(1048576)
                    if not chunk:break
                    size+=len(chunk);need(size<=268435456,'BOUNDED_RESULT_REQUIRED');digest.update(chunk);dst.write(chunk)
            need(digest.hexdigest()==expected,'GENERATOR_RESULT_HASH_CHANGED')
            dest=target/name
            if dest.exists():
                need(not dest.is_symlink() and hashlib.sha256(dest.read_bytes()).hexdigest()==expected,
                     'DIFFERENT_RETURNED_RESULT_PRESERVED')
            else:acl(Path(tmp),reader);os.replace(tmp,dest)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return {'status':'PASS_STOPPED_GENERATOR_RESULTS_READABLE_BY_B','directory':str(target),
        'stopped_receipt':str(target/'receipt.json'),'load_status':receipt['status'],
        'b_config_or_credentials_read':False,'cloud_calls':0}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preparation',required=True,type=Path)
    p.add_argument('--plan',required=True,type=Path);p.add_argument('--work',required=True,type=Path)
    p.add_argument('--return-root',required=True,type=Path);p.add_argument('--node')
    p.add_argument('--execute',action='store_true');p.add_argument('--return-only',action='store_true');a=p.parse_args();os.umask(0o077)
    try:
        setup=read(a.preparation);need(pwd.getpwuid(os.getuid()).pw_name==setup['reader'],'EXPECTED_D_OS_ACCOUNT_REQUIRED')
        plan=read(a.plan);need(re.fullmatch(r'[a-z0-9-]{3,46}',plan['run_id']),'EXACT_PLAN_RUN_ID_REQUIRED')
        code=Path(setup['code_dir']);guard=plan['guard_argv']
        need(len(guard)>=3 and Path(guard[2])==code/'control.py','SAME_READ_ONLY_LOCAL_GUARD_REQUIRED')
        for name in ('load.mjs','control.py','common.py'):
            raw=(code/name).read_bytes().replace(b'\r\n',b'\n')
            need(hashlib.sha256(raw).hexdigest()==setup['source_sha256'][name],'SAME_ADOPTED_GENERATOR_REQUIRED')
        if not a.execute:print(json.dumps({'status':'PREPARED_D_LOCAL_GENERATOR_NO_EXECUTION','cloud_calls':0}));return 0
        if not a.return_only:
            candidates=[a.node] if a.node else [shutil.which('node'),str(Path.home()/'hpa-load/node/bin/node'),str(Path.home()/'hpa-load/node')]
            node=next((s for s in candidates if s and Path(s).is_file() and os.access(s,os.X_OK)),None)
            need(node is not None,'EXISTING_D_NODE_EXECUTABLE_REQUIRED')
            need(not a.work.exists(),'EXISTING_GENERATOR_RUN_PRESERVED_DO_NOT_REPEAT')
            need(a.work.parent.is_dir(),'EXISTING_GENERATOR_RESULT_PARENT_REQUIRED')
            # Node's existing plan validator enforces the remaining window/guard.
            result=subprocess.run([node,str(code/'load.mjs'),'--plan',str(a.plan),'--out',str(a.work),
                '--abort-file',str(a.work.with_suffix('.abort')),'--execute'])
            need((a.work/'receipt.json').is_file(),'GENERATOR_OUTCOME_UNCONFIRMED_B_MUST_RECONCILE')
        print(json.dumps(return_results(plan,a.work,a.return_root,setup['owner'])));return 0
    except Exception as e:
        print(json.dumps({'status':'STOP_D_GENERATOR_HANDOFF','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
            'automatic_retry':False,'cloud_calls':0}));return 2

if __name__=='__main__':raise SystemExit(main())
