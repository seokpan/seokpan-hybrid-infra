"""Task-key metadata transport. Delegate original exact rolling/guard commands unchanged."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile

HERE=Path(__file__).resolve().parent

def need(ok,code):
    if not ok:raise RuntimeError(code)
def read(path):
    p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_size<=1048576,'OWNED_METADATA_REQUIRED')
    need(p.stat().st_uid==os.getuid(),'OWNED_METADATA_REQUIRED')
    return json.loads(p.read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()

def accepted_dispatch_hashes(c):
    values={c['original_dispatch_sha256'],*c.get('original_dispatch_sha256_variants',[])}
    need(1<=len(values)<=2 and all(isinstance(x,str) and re.fullmatch(r'[0-9a-f]{64}',x) for x in values),
         'EXACT_REVIEWED_DISPATCH_HASHES_REQUIRED')
    return values

def install(settings):
    c=read(settings);p=Path.home()/'.ssh/authorized_keys'
    need(p.is_file() and not p.is_symlink() and p.stat().st_uid==os.getuid(),'EXISTING_OWNED_TASK_KEY_REQUIRED')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW);fcntl.flock(fd,fcntl.LOCK_EX)
    try:
        raw=p.read_text();lines=raw.splitlines(keepends=True)
        indexes=[i for i,line in enumerate(lines) if c['public_key'] in line]
        need(len(indexes)==1,'EXACT_EXISTING_TASK_KEY_REQUIRED');i=indexes[0]
        match=re.search(r'command="([^"]+)"',lines[i]);need(match is not None,'EXISTING_FORCED_COMMAND_REQUIRED')
        forced=shlex.join([c['python'],'-I',str(HERE/'metadata_transport.py'),'--settings',str(settings)])
        if match.group(1)==forced:
            previous=read(HERE/'original-dispatch.json')
            need(sha(previous['argv'][2]) in accepted_dispatch_hashes(c),'ORIGINAL_RESTRICTIONS_CHANGED')
            return {'status':'REUSED_TASK_METADATA_TRANSPORT','new_shell_access':False}
        original=shlex.split(match.group(1))
        need(len(original)==3 and original[:2]==[c['python'],'-I']
            and sha(original[2]) in accepted_dispatch_hashes(c),'EXACT_ORIGINAL_RESTRICTED_DISPATCH_REQUIRED')
        receipt=HERE/'original-dispatch.json';need(not receipt.exists(),'PARTIAL_TRANSPORT_INSTALL_REQUIRES_RECONCILIATION')
        delegation={'argv':original}
        if c.get('reviewed_delegate'):
            reviewed=c['reviewed_delegate'];target=HERE/'restricted_delegate.py'
            wanted=[c['python'],'-I',str(target)]
            need(reviewed.get('argv')==wanted and target.is_file() and not target.is_symlink()
                and target.stat().st_uid==os.getuid() and sha(target)==reviewed.get('sha256')
                and sha(target) in accepted_dispatch_hashes(c),'EXACT_REVIEWED_DELEGATE_REQUIRED')
            # Keep the observed old file unchanged; do not execute unknown old code.
            delegation={'argv':wanted,'prior_argv':original,'prior_dispatch_sha256':sha(original[2]),
                        'delegation':'REVIEWED_PROBE_AND_TWO_EXACT_COMMANDS'}
        handle=os.open(receipt,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(handle,'w') as f:json.dump(delegation,f)
        lines[i]=lines[i][:match.start(1)]+forced+lines[i][match.end(1):]
        handle,tmp=tempfile.mkstemp(prefix='.task-key-',dir=p.parent)
        with os.fdopen(handle,'w') as f:f.writelines(lines);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
        return {'status':'PASS_TASK_METADATA_TRANSPORT_INSTALLED','new_shell_access':False,'other_keys_changed':False}
    finally:os.close(fd)

def route(command,settings):
    if command in {'b-hpa-evidence-summary-v1','b-hpa-evidence-archive-v1',
                    'b-hpa-publication-proof-v1'}:
        import importlib.util
        helper=HERE/'public_hpa_export.py'
        need(sha(helper)=='f3485eb9af1dbd0dde197807d24574f61484e40de2ebadd127ddc2a2d4716929','PUBLIC_EXPORT_SOURCE_CHANGED')
        spec=importlib.util.spec_from_file_location('hpa_public_export',helper)
        obj=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(obj)
        return obj.route(command)
    c=read(settings);args=shlex.split(command);run=Path(c['run']);out=Path(c['out'])
    if args==['b-prestart-report-v1']:
        print(json.dumps(read(out/'receipt.json')));return 0
    if args==['b-browser-inputs-v1']:
        # Browser profiles and one rolling argv only; never DB configs/kubeconfig.
        names=['baseline.json','rolling.json','trigger.json','binding-receipt.json']
        print(json.dumps({name:read(run/'measurement-bindings'/name) for name in names}));return 0
    if len(args)==2 and args[0] in {'b-stage-request-v1','b-stage-result-v1'}:
        stage=args[1];need(stage in {'single','shared','target'},'EXACT_STAGE_REQUIRED')
        request=run/'clocks'/(stage+'.external-request.json')
        if args[0]=='b-stage-request-v1':print(json.dumps(read(request)));return 0
        raw=sys.stdin.buffer.read(65537);need(len(raw)<=65536,'BOUNDED_EXTERNAL_PROOF_REQUIRED')
        value=json.loads(raw);wanted=read(request)
        need(value.get('status')=='PASS_EXTERNAL_HTTPS_WSS_INITIAL_FRAME' and value.get('binding')==wanted['binding']
            and value.get('nonce')==wanted['nonce'] and value.get('target_origin_sha256')==wanted['binding']['origin_sha256']
            and value.get('https_status')==200 and value.get('wss_initial_envelope') is True,'EXACT_EXTERNAL_RESULT_BINDING_REQUIRED')
        need(set(value)=={'schema','status','binding','nonce','target_origin_sha256','https_status','wss_initial_envelope',
            'elapsed_ms','observed_utc','guest_session_created','accounts_or_rooms_created'},'EXACT_EXTERNAL_PROOF_FIELDS_REQUIRED')
        inbox=run/'external-results';inbox.mkdir(mode=0o700,exist_ok=True)
        target=inbox/(stage+'.json')
        if target.exists():need(read(target)==value,'EXISTING_EXTERNAL_RESULT_DIFFERS_NO_OVERWRITE')
        else:
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(value,f)
        print(json.dumps({'status':'RECEIVED_BOUND_EXTERNAL_RESULT_NOT_CLOCK_ACCEPTANCE','stage':stage}));return 0
    previous=read(HERE/'original-dispatch.json')
    need(sha(previous['argv'][2]) in accepted_dispatch_hashes(c),'ORIGINAL_RESTRICTIONS_CHANGED')
    # Unknown commands still go through the original exact allowlist, not a shell.
    os.execv(previous['argv'][0],previous['argv'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--settings',required=True,type=Path);p.add_argument('--install',action='store_true');a=p.parse_args()
    try:
        if a.install:print(json.dumps(install(a.settings)));return 0
        return route(os.environ.get('SSH_ORIGINAL_COMMAND',''),a.settings)
    except Exception as e:
        print(json.dumps({'status':'STOP_TASK_METADATA_TRANSPORT','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
            'cloud_mutations':0}));return 2

if __name__=='__main__':raise SystemExit(main())

