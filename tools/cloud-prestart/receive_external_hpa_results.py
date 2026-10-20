"""메신저로 받은 동일 결과를 제한된 입력 경로로 인수하고 기존 종료 수집을 잇습니다."""
import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import zipfile

ROOT = Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN = ROOT/'controller-run-bundle-K2Tx7HgB'
BASE = RUN/'prestart-closeout'
NAMES = {'SHA256SUMS.json','receipt.json','counts.json','requests.jsonl','events.jsonl','generator.jsonl'}
COLLECTOR_NAME = 'external_hpa_collector.py'

def need(ok, code):
    if not ok:
        raise RuntimeError(code)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def raw(path, maximum=33554432):
    fd = os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
             and info.st_size<=maximum,'OWNED_BOUNDED_RECORD_REQUIRED')
        return stream.read(maximum+1)

def read(path):
    return json.loads(raw(path))

def canon(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def save_same(path, data):
    if path.exists():
        need(raw(path)==data,'EXISTING_EVIDENCE_PRESERVED_NO_OVERWRITE')
        return
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data)

def unpack(data, expected_hash, config, config_hash):
    need(sha(data)==expected_hash,'EXTERNAL_ARCHIVE_HASH_MISMATCH')
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            mode = member.external_attr>>16
            need(member.filename in NAMES and member.filename not in files
                 and not member.is_dir() and not stat.S_ISLNK(mode)
                 and member.file_size<=16777216,'EXACT_SIX_REGULAR_RESULT_FILES_REQUIRED')
            files[member.filename] = archive.read(member)
    need(set(files)==NAMES and sum(map(len,files.values()))<=33554432,
         'EXACT_BOUNDED_RESULT_SET_REQUIRED')
    manifest = json.loads(files['SHA256SUMS.json'])
    need(set(manifest)==NAMES-{'SHA256SUMS.json'}
         and all(sha(files[name])==value for name,value in manifest.items()),
         'EXTERNAL_INTERNAL_HASH_MISMATCH')
    receipt = json.loads(files['receipt.json'])
    counts = json.loads(files['counts.json'])
    need(receipt['run_id']==config['run_id'] and receipt['binding']==config['binding']
         and receipt['control_sha256']==config_hash
         and counts['run_id']==receipt['run_id'] and counts['binding']==receipt['binding']
         and counts['control_sha256']==config_hash,'SAME_ACCEPTED_RUN_REQUIRED')
    need(receipt['status'] in ('COMPLETED_HPA_LOAD','ABORTED_HPA_LOAD')
         and receipt['in_flight_at_end']==0,'ACTUAL_STOPPED_GENERATOR_REQUIRED')
    return files

def current():
    active = read(RUN/'hpa-active-time-copy.json')
    directory = Path(active['directory'])
    need(not directory.is_symlink() and directory.resolve().is_relative_to(RUN.resolve()),
         'EXACT_ACTIVE_RUN_DIRECTORY_REQUIRED')
    config = read(Path(active['config']))
    accepted = read(directory/'receipt.json')
    need(sha(canon(config))==accepted['config_sha256'],'ACCEPTED_CONFIG_REQUIRED')
    return active,directory,config,accepted

def install(settings, collector):
    need(re.fullmatch(r'[0-9a-f]{64}',settings['archive_sha256']) is not None,
         'EXACT_EXTERNAL_ARCHIVE_HASH_REQUIRED')
    active,directory,config,accepted = current()
    need(settings['run_id']==config['run_id'],'SAME_RUN_SETTINGS_REQUIRED')
    data = raw(collector,1048576)
    need(sha(data)==settings['collector_sha256'],'EXTERNAL_COLLECTOR_SOURCE_REQUIRED')
    save_same(BASE/COLLECTOR_NAME,data)
    source = raw(Path(__file__).resolve(),1048576)
    save_same(BASE/'receive_external_hpa_results.py',source)
    save_same(BASE/'external-result-settings.json',canon(settings)+b'\n')
    transport = BASE/'metadata_transport.py'
    original = raw(transport,1048576)
    hook = """    if command in {'b-external-d-results-v1','b-final-delete-plan-summary-v1'}:
        helper=HERE/'receive_external_hpa_results.py'
        need(sha(helper)=='FINGERPRINT','EXTERNAL_RESULT_RECEIVER_SOURCE_CHANGED')
        mode='--receive' if command=='b-external-d-results-v1' else '--plan-summary'
        os.execv(sys.executable,[sys.executable,'-I',str(helper),mode])
""".replace('FINGERPRINT',sha(source))
    if b'b-external-d-results-v1' not in original:
        need(sha(original)==settings['metadata_before_sha256'],
             'EXACT_EXISTING_METADATA_TRANSPORT_REQUIRED')
        needle = b'def route(command,settings):\n'
        need(original.count(needle)==1,'EXACT_METADATA_ROUTE_REQUIRED')
        updated = original.replace(needle,needle+hook.encode())
        save_same(BASE/'metadata_transport.before-external-results.py',original)
        fd,temp = tempfile.mkstemp(prefix='.external-result-route-',dir=BASE)
        with os.fdopen(fd,'wb') as stream:
            stream.write(updated);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,transport)
    else:
        need(hook.encode() in original,'EXISTING_EXTERNAL_ROUTE_CHANGED')
    return {'status':'PASS_EXTERNAL_RESULT_RECEIVER_SOURCE_ONLY_CONNECTED',
            'authorized_keys_changed':False,'cloud_calls':0,'sql_calls':0,
            'tests_repeated':False,'actual_delete_started':False}

def receive():
    active,directory,config,accepted = current()
    settings = read(BASE/'external-result-settings.json')
    payload = sys.stdin.buffer.read(4194305)
    need(len(payload)<=4194304,'BOUNDED_ARCHIVE_INPUT_REQUIRED')
    value = json.loads(payload)
    need(set(value)=={'sha256','base64'} and value['sha256']==settings['archive_sha256'],
         'EXACT_EXPECTED_EXTERNAL_ARCHIVE_REQUIRED')
    data = base64.b64decode(value['base64'],validate=True)
    files = unpack(data,settings['archive_sha256'],config,accepted['config_sha256'])
    local = read(directory/'b-local-final-closeout/receipt.json')
    need(local['status']=='PASS_B_LOCAL_EVIDENCE_PRESERVED_D_EXTERNAL_PENDING'
         and local['summary']['config_sha256']==accepted['config_sha256']
         and local['summary']['B_reader_stop']['status']=='STOPPED_OWNED_B_READERS',
         'ACTUAL_B_READER_STOP_AND_SAME_CONFIG_REQUIRED')
    need(sha(raw(Path(local['public_archive'])))==local['public_archive_sha256'],
         'B_PUBLIC_EVIDENCE_HASH_CHANGED')
    work = Path(active['work'])
    need(raw(work/'platform-db-metrics.jsonl')==raw(directory/'b-local-final-closeout/B-metrics.private.jsonl'),
         'B_OBSERVATIONS_CHANGED_AFTER_CAPTURE')
    target = directory/'external-d-results'
    target.mkdir(mode=0o700,exist_ok=True)
    need(not target.is_symlink() and target.stat().st_uid==os.getuid(),
         'OWNED_EXTERNAL_RESULT_DIRECTORY_REQUIRED')
    save_same(target/'received.zip',data)
    for name,content in files.items():
        save_same(target/name,content)
    source = BASE/COLLECTOR_NAME
    need(sha(raw(source))==settings['collector_sha256'],'EXACT_EXTERNAL_COLLECTOR_REQUIRED')
    spec = importlib.util.spec_from_file_location('external_final_collector',source)
    helper = importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    result = helper.finish(returned_override=target)
    result = dict(result)
    result['transport'] = 'MESSENGER_ARCHIVE_VERIFIED_THEN_B_SCOPED_STDIN'
    result['old_D_return_path_accessed'] = False
    return result

def plan_summary():
    directory = Path(read(RUN/'b-final-delete-plan.json')['directory'])
    need(directory.resolve().parent==ROOT.resolve(),'EXACT_B_PLAN_DIRECTORY_REQUIRED')
    manifest = read(directory/'plan-manifest.json')
    need(sha(raw(Path(manifest['plan'])))==manifest['plan_sha256'],'SAVED_PLAN_HASH_CHANGED')
    plan = read(directory/'plan-private.json')
    changes = [{'address':x['address'],'type':x['type'],'actions':x['change']['actions']}
               for x in plan.get('resource_changes',[]) if x.get('mode')=='managed'
               and x['change']['actions']!=['no-op']]
    return {'status':'COLLECTED_EXISTING_B_DELETE_PLAN_ONLY','receipt':read(directory/'receipt.json'),
            'cluster_id':manifest['cluster_id'],'plan_sha256':manifest['plan_sha256'],
            'state_serial':manifest['state_serial'],'changes':changes,
            'plan_repeated':False,'apply_started':False}

if __name__=='__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--install',action='store_true')
    group.add_argument('--receive',action='store_true')
    group.add_argument('--plan-summary',action='store_true')
    parser.add_argument('--settings',type=Path)
    parser.add_argument('--collector',type=Path)
    args = parser.parse_args()
    try:
        import pwd
        need(pwd.getpwuid(os.getuid()).pw_name=='jth','USE_CONTROLLER_JTH')
        result = install(read(args.settings),args.collector) if args.install else receive() if args.receive else plan_summary()
        print(json.dumps(result,indent=2))
    except Exception as error:
        print(json.dumps({'status':'STOP_EXTERNAL_HPA_EVIDENCE',
            'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'automatic_retry':False,'actual_delete_started':False}))
        raise SystemExit(2)

