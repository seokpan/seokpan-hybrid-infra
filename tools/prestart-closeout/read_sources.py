"""Read only failed transport metadata and existing input locations. No adoption or APIs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

FOUNDATION = {'vpc_id','network_subnets','rosa_account_role_arns','rosa_operator_policy_arns',
              'rds_security_group_id','redis_security_group_id'}
PRUNE = {'.git','.terraform','tf-data','node_modules','__pycache__','source','repository',
         'app-measurement','hpa-cutover','measurement-bindings','protected'}

def read(path):
    path=Path(path);before=path.lstat()
    if not stat.S_ISREG(before.st_mode):raise RuntimeError('BOUNDED_REGULAR_FILE_REQUIRED')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_size>2097152 or (s.st_dev,s.st_ino)!=(before.st_dev,before.st_ino):
            raise RuntimeError('BOUNDED_REGULAR_FILE_REQUIRED')
        return f.read(2097153)

def nodes(value,depth=0):
    if depth>12:return
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from nodes(v,depth+1)
    elif isinstance(value,list):
        for v in value:yield from nodes(v,depth+1)

def kinds(value):
    found=set()
    for d in nodes(value):
        status=d.get('status')
        if FOUNDATION<=set(d):found.add('foundation_handoff')
        if status=='SOURCE_GIT_CONTROL_CONTRACT_COLLECTED':found.add('source_contract')
        if status=='SOURCE_LOCAL_READ_COMPLETED':found.add('source_read')
        if 'categories' in d and 'inventory_complete_confirmed_by_operator' in d:found.add('writer_plan')
        if isinstance(d.get('budget'),dict) and isinstance(d.get('window'),dict):found.add('cost_and_window')
        if {'source_cluster_id','foundation_handoff','recreate_inputs_sha256'}<=set(d):found.add('recreate_readiness')
        if {'global_limit','reserved','accounts'}<=set(d):found.add('capacity')
        if {'max_connections','max_user_connections'}<=set(d):found.add('db_limits_evidence')
    return sorted(found)

def scan(roots,max_files=5000,max_depth=6):
    candidates={};errors=[];seen=set();count=0;truncated=False
    for root in roots:
        root=Path(root).expanduser()
        if root.is_symlink() or not root.is_dir():
            errors.append({'root':str(root),'reason':'NOT_READABLE_REGULAR_DIRECTORY'});continue
        def walk_error(error):
            errors.append({'path':error.filename,'reason':'DIRECTORY_READ_DENIED'})
        for folder,dirs,names in os.walk(root,followlinks=False,onerror=walk_error):
            folder=Path(folder);depth=len(folder.relative_to(root).parts)
            dirs[:]=[x for x in dirs if x not in PRUNE and not (folder/x).is_symlink() and depth<max_depth]
            for name in sorted(names):
                lower=name.lower()
                if not lower.endswith('.json') or any(x in lower for x in
                    ('.sops.','template','-tree.json','publication-tree','token','credential','secret','kubeconfig')):continue
                p=folder/name
                if str(p) in seen:continue
                seen.add(str(p));count+=1
                if count>max_files:truncated=True;break
                try:
                    raw=read(p);value=json.loads(raw.decode('utf-8-sig'));labels=kinds(value)
                except PermissionError:
                    errors.append({'path':str(p),'reason':'FILE_READ_DENIED'});continue
                except (OSError,ValueError,UnicodeError,RuntimeError):continue
                if labels:
                    row={'path':str(p),'sha256':hashlib.sha256(raw).hexdigest(),
                         'nested_mapping_only':not isinstance(value,dict) or not FOUNDATION<=set(value),
                         'currentness':'NOT_ASSERTED','adopted':False}
                    for label in labels:candidates.setdefault(label,[]).append(row)
            if truncated:break
        if truncated:break
    groups={k:{'files':v[:30],'truncated':len(v)>30,
        'distinct_contents':len({x['sha256'] for x in v}),'auto_adopted':False} for k,v in candidates.items()}
    return {'candidates':groups,'read_errors':errors[:30],'error_list_truncated':len(errors)>30,
            'files_considered':min(count,max_files),'scan_truncated':truncated}

def transport(run):
    base=Path(run)/'prestart-closeout'
    result={'registration_retried':False,'private_key_or_authorized_keys_read':False}
    for label,path in [('failure',base/'inputs/metadata-transport-receipt.json'),
                       ('settings',base/'transport-settings.json')]:
        try:
            value=json.loads(read(path).decode('utf-8-sig'))
            if label=='failure':
                reason=value.get('reason','NO_REASON_FIELD')
                result['reason']=reason if isinstance(reason,str) and re.fullmatch(r'[A-Za-z0-9_]{1,200}',reason) else 'PRIVATE_DETAIL_NOT_PRINTED'
                result['status']=value.get('status') if value.get('status') in {
                    'PENDING_TASK_METADATA_TRANSPORT','PASS_TASK_METADATA_TRANSPORT_INSTALLED','REUSED_TASK_METADATA_TRANSPORT'} else 'STATUS_NOT_CLASSIFIED'
            else:expected=value.get('original_dispatch_sha256')
        except (OSError,ValueError,UnicodeError,RuntimeError):
            result[label+'_read']='NOT_AVAILABLE'
            if label=='settings':expected=None
    try:
        raw=read(Path(run)/'generator-link/restricted_dispatch.py').replace(b'\r\n',b'\n')
        result['dispatcher_sha256']=hashlib.sha256(raw).hexdigest()
        result['matches_installed_expected_hash']=result['dispatcher_sha256']==expected
        result['matches_exact_single_leading_lf_variant']=bool(expected and raw.startswith(b'\n') and
            not raw.startswith(b'\n\n') and hashlib.sha256(raw[1:]).hexdigest()==expected)
    except (OSError,RuntimeError):result['dispatcher_read']='NOT_AVAILABLE'
    result['partial_install_receipt_present']=(base/'original-dispatch.json').exists()
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--settings',type=Path,required=True)
    p.add_argument('--search-root',action='append',default=[])
    a=p.parse_args();os.umask(0o077)
    c=json.loads(read(a.settings).decode('utf-8-sig'))
    report={'status':'COLLECTED_P05_P09_EXISTING_SOURCES_NOT_ADOPTED',
        'transport':transport(c['run']),**scan([c['root'],*a.search_root]),
        'remote_host_sources':'NOT_SCANNED_BY_LOCAL_FILE_READER',
        'cloud_calls':0,'sql_calls':0,'decrypt_invocations':0,'acl_changes':0,
        'source_inputs_changed':False,'recreation_started':False}
    out=Path(tempfile.mkdtemp(prefix='p05-p09-read-',dir=c['root']))
    target=out/'receipt.json'
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps(report,indent=2));print('Private result directory: '+str(out))
    return 0

if __name__=='__main__':raise SystemExit(main())
