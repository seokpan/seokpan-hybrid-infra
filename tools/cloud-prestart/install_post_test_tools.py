"""기존 키와 설정을 유지하고 종료 도구 및 공개 증거 전달만 연결합니다."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time

HERE=Path(__file__).resolve().parent
RUN=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62/controller-run-bundle-K2Tx7HgB'
BASE=RUN/'prestart-closeout'
OLD_TRANSPORT_SHA='bfbcebc5dce8c796cdbe8a7b5c14100d1bd84d2d353f76f0b71838366a7d1739'

def need(ok,code):
    if not ok:
        raise RuntimeError(code)

def raw(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
             and info.st_size<=1048576,'OWNED_SMALL_TOOL_REQUIRED')
        return stream.read(1048577)

def adopt(target,data,allowed_old=None):
    if target.exists():
        current=raw(target)
        if current==data:
            return 'REUSED_IDENTICAL_SOURCE'
        need(allowed_old is not None and hashlib.sha256(
             current.replace(b'\r\n',b'\n')).hexdigest() in
             ({allowed_old} if isinstance(allowed_old,str) else set(allowed_old)),
             'UNEXPECTED_EXISTING_SOURCE_PRESERVED')
        backup=target.with_name(target.name+'.before-public-export-'+str(time.time_ns()))
        fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(current)
    fd,temp=tempfile.mkstemp(prefix='.post-test-tool-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return 'ADOPTED_SOURCE_ONLY'

def install():
    need(os.getuid()!=0 and Path.home().name=='jth','USE_CONTROLLER_JTH')
    need(BASE.is_dir() and not BASE.is_symlink(),'EXISTING_PRESTART_DIRECTORY_REQUIRED')
    result={}
    old_sources={
        'public_hpa_export.py':'0b4c24d20cf81ff86ab94217c7f335a34a0eb0e4c8473e05262790c4d0554511',
        'prepare_b_final_delete.py':'24d106397c7106dd4bd928b6d48de7edd58fc99ba2e7ef1daccfa198f1cb06a9',
        'apply_b_final_delete.py':'90faf7edd4e9e05b33cf23a5a9ccaf165ca435ce34af2a7686d5e59ccf52cffb'}
    # 모든 입력과 기존 대상이 수락되는지 먼저 확인해 부분 설치를 방지합니다.
    payload=[]
    for name in ('public_hpa_export.py','finish_hpa_time_copy.py','finish_hpa_final_teardown.py',
                 'prepare_b_final_delete.py','apply_b_final_delete.py',
                 'b_final_residuals.py','run_b_final_delete_plan.sh'):
        payload.append((name,raw(HERE/name),old_sources.get(name)))
    payload.append(('metadata_transport.py',raw(HERE/'post_test_metadata_transport.py'),
        {OLD_TRANSPORT_SHA,'c32634e65f6eaed17afde72accd8185fa75bfe632c3708b1147b4363622e3d63'}))
    for name,data,allowed in payload:
        target=BASE/name
        if target.exists():
            current=raw(target)
            accepted={allowed} if isinstance(allowed,str) else set(allowed or ())
            need(current==data or hashlib.sha256(current.replace(b'\r\n',b'\n')).hexdigest()
                 in accepted,'UNEXPECTED_EXISTING_SOURCE_PRESERVED')
    # 저장소의 명시적 파일명을 사용하고 설치 대상 이름은 그대로 유지합니다.
    for name,data,allowed in payload:
        result[name]=adopt(BASE/name,data,allowed)
    return {'status':'PASS_POST_TEST_TOOLS_CONNECTED_NOT_EXECUTED','files':result,
        'authorized_keys_changed':False,'settings_changed':False,
        'acl_or_installation_repeated':False,'runtime_tests_repeated':False,
        'api_calls':0,'sql_calls':0,'processes_restarted':0,
        'hpa_stopped':False,'project_deletion_started':False}

if __name__=='__main__':
    os.umask(0o077)
    try:
        print(json.dumps(install(),indent=2))
    except Exception as error:
        print(json.dumps({'status':'STOP_POST_TEST_TOOL_CONNECTION',
            'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'automatic_retry':False}))
        raise SystemExit(2)

