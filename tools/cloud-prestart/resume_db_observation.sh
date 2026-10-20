(
  # 실행 위치: Controller(ansible)의 jth 터미널
  # 기존 성공 기록은 유지하고, 실행하지 못한 DB 관측 한 번만 수행합니다.
  # 다운로드·설치·앱 Sync·모니터링 Sync·계정 변경은 하지 않습니다.
  set +x
  set -euo pipefail
  umask 077
  [[ "$(id -un)" == jth ]] || { echo STOP_USE_CONTROLLER_JTH; exit 1; }

  python3 -I - <<'PY'
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

os.umask(0o077)
root=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
run=root/'controller-run-bundle-K2Tx7HgB'
out=run/'current-monitoring-closeout-20261020'
tool=root/'current-runtime-tools-Nl3HF99g/closeout.py'

def need(ok,code):
    if not ok: raise RuntimeError(code)

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value

def sha(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()

try:
    need(tool.is_file() and not tool.is_symlink(),'EXISTING_CLOSEOUT_TOOL_REQUIRED')
    need(sha(tool)=='badcd318fd44ec7de85bd773023fbbdf15f91ce5fa6e41204e27df5afd3fc885','SOURCE_HASH_MISMATCH')
    M=module('accepted_closeout',tool)
    lock=open(out/'run.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    report=M.read(out/'receipt.json')
    need(report['cluster_id']==M.CID,'SAME_CLUSTER_RECEIPT_REQUIRED')
    need(report['monitoring']['status']=='SYNCED_HEALTHY' and report['scrape']['status']=='PASS_CURRENT_BACKEND_UP','ACCEPTED_MONITORING_RECORD_REQUIRED')
    dispatch=M.read(run/'current-fresh-app-run.json')
    cfg=M.read(dispatch['config']);work=Path(dispatch['work'])
    inputs=M.read(work/'new-inputs.json')['result']['inputs']
    need(inputs['cluster_id']==M.CID and inputs['namespace_uid']==M.NSUID,'SAME_BOUND_INPUT_REQUIRED')
    source=M.read(work/'package/single.source.json')
    hpa=run/'hpa-cutover/tools/hpa-load'
    common=module('existing_hpa_common',hpa/'common.py')
    binding={'cluster_id':M.CID,'namespace_uid':M.NSUID,'application_uid':M.APPUID,
             'normalized_source_sha256':common.digest(common.normalized(source))}
    observer_path=run/'prestart-closeout/pod_db_observer.py'
    need(sha(observer_path)=='bb15dc60e7b32bcf12bc46966b93dc0aeddb41f19e4f6f4f97ceab79d2e4f030','EXISTING_DB_OBSERVER_SOURCE_REQUIRED')
    measure=run/'app-measurement/tools/presentation-measurement'
    deadline=time.monotonic()+90
    now=dt.datetime.now(dt.timezone.utc)
    image=next(x['image'] for x in report['deployments']['backend']['containers'] if x['name']=='backend')
    db={'binding':binding,'database_host':inputs['database_host'],'api':inputs['api_url'],
        'oc':cfg['oc'],'personal_kubeconfig':cfg['personal_kubeconfig'],
        'schema_credentials_file':cfg['schema_credentials_file'],'backend_image':image,
        'hpa_source':str(hpa),'common_sha256':sha(hpa/'common.py'),
        'measurement_source':str(measure),'db_observation_sha256':sha(measure/'db_observation.py'),
        'interval_seconds':10,'duration_seconds':30,
        'not_after':(now+dt.timedelta(seconds=90)).isoformat()}
    M.save(out/'db-observer.json',db)

    # 19시 이후 계속 진행하라는 지시에 따라 DB 관측에만 최대 90초를 부여합니다.
    # 기존 전체 실행시간·비용·부하 시험 설정은 수정하지 않습니다.
    def bounded_runner(*args,**kwargs):
        left=deadline-time.monotonic()
        need(left>0,'DB_OBSERVATION_90_SECOND_BOUND_REACHED')
        kwargs['timeout']=min(kwargs.get('timeout',18),left)
        return subprocess.run(*args,**kwargs)

    if (out/'db-sample.json').exists():
        sample=M.read(out/'db-sample.json')
        need(sample.get('status')=='PASS_CURRENT_DB_SAMPLE' and sample.get('binding')==binding,'ACCEPTED_SAME_BINDING_SAMPLE_REQUIRED')
        reused=True
    else:
        print(json.dumps({'phase':'DB_OBSERVATION_ONLY','maximum_seconds':90}),flush=True)
        sample=module('accepted_db_observer',observer_path).sample(db,runner=bounded_runner)
        M.save(out/'db-sample.json',sample);reused=False

    report['db_observer']=sample
    report['db_observer_config']=str(out/'db-observer.json')
    report['remaining']=[x for x in report['remaining'] if x!='DB_OBSERVER_ACTUAL_ACCEPTANCE']
    report['status']='PASS_B_RUNTIME_CLOSEOUT_NOT_LOAD_TEST' if not report['remaining'] else 'PARTIAL_B_RUNTIME_CLOSEOUT'
    report['window_extended']=True
    report['extension_scope']='USER_AUTHORIZED_DB_ONLY_MAXIMUM_90_SECONDS'
    report['remaining_seconds']=0
    M.save(out/'db-only-result.json',{'status':report['status'],'sample':sample,'reused':reused,
        'source_script':str(observer_path),'maximum_seconds':90,'load_test_started':False})
    M.save(out/'receipt.json',report);M.save(out/'D-runtime-handoff.json',report)
    print(json.dumps({'status':report['status'],'db_observer':sample,'sample_reused':reused,
        'remaining':report['remaining'],'app_or_monitoring_sync_repeated':False,
        'account_or_schema_changes':0,'load_test_started':False},indent=2))
except Exception as error:
    result={'status':'STOP_DB_OBSERVATION_ONLY','reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'automatic_retry':False,'raw_credentials_or_logs_printed':False,
            'app_and_monitoring_success_preserved':True}
    if out.is_dir():
        p=out/'db-only-failure.json'
        with open(p,'w',encoding='utf-8') as stream: json.dump(result,stream,indent=2)
        p.chmod(0o600)
    print(json.dumps(result,indent=2));raise SystemExit(2)
finally:
    print('Private closeout directory: '+str(out))
PY
)
