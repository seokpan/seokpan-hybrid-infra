"""Bind actual new values to the prepared transport; never launches tests or APIs."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

HERE=Path(__file__).resolve().parent

def need(ok,code):
    if not ok:raise RuntimeError(code)
def read(path):return json.loads(Path(path).read_text())
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def save(path,value):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    if path.exists():need(path.read_bytes()==raw,'EXISTING_BOUND_FILE_PRESERVED');return
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw)

def hpa_ceiling(q,minimum):
    per=q['processes']*(q['pool_size']+q['overflow']);extra=q['surge_allowance']+q['terminating_allowance']
    need(per>0 and q['reserved']>=0,'CURRENT_POOL_AND_RESERVATION_REQUIRED')
    peak=min(q['node_backend_capacity'],(q['global_limit']-q['reserved'])//(2*per))
    for v in q['accounts'].values():
        cap=v['max_user_connections'] or v['global_max_user_connections']
        if cap:peak=min(peak,cap//per)
    hi=min(10,peak-extra);need(hi>=minimum,'NO_SAFE_HPA_RANGE_KEEP_NORMAL_APP_ONLY')
    return hi

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--settings',required=True,type=Path)
    p.add_argument('--stage',choices=['single','shared','target'],required=True);p.add_argument('--business-ref',required=True)
    p.add_argument('--fixed-comparison-ref');a=p.parse_args();os.umask(0o077)
    try:
        s=read(a.settings);root=Path(s['out']);run=Path(s['run']);app=Path(s['app_source']);ops=Path(s['ops_source'])
        work=run/'cloud-ready';pre=read(root/'prestart.NOT_ACCEPTED.json');paths=read(root/'execution-paths.NOT_ACCEPTED.json')
        sys.path.insert(0,str(app/'tools/presentation-measurement'))
        B=module('closing_bind_inputs',app/'tools/presentation-measurement/bind_inputs.py');L=B.load_launch(app)
        ready=read(work/(a.stage+'-ready.json'))
        result=B.bind(L,work/'package',a.stage,ready,a.business_ref,paths,pre)
        rolling=result['rolling-config.json'];launch=read(work/'package/inputs.private.json')
        # Existing binder's MariaDB files are deliberately replaced by a supported
        # Pod transport. No fake MariaDB executable/file path is supplied.
        pending=[k for k in result['binding-receipt.json']['missing_paths'] if k not in
            {'db_mariadb','db_credential_file','db_ca_file','db_target_reference'}]
        need(not pending,'NON_DB_MEASUREMENT_EXECUTION_PATHS_REQUIRED')
        schema=Path(pre['schema_credentials_file']);need(schema.is_file(),'PREPARED_SCHEMA_CREDENTIAL_FILE_REQUIRED')
        common_hash=s['db_bridge']['source_sha256']['common.py']
        base={'oc':pre['oc'],'personal_kubeconfig':pre['personal_kubeconfig'],'api':launch['api_url'],
            'schema_credentials_file':str(schema),'database_host':launch['database_host'],
            'backend_image':rolling['expected_images']['backend'],'hpa_source':str(ops/'tools/hpa-load'),
            'common_sha256':common_hash,'interval_seconds':paths['db_interval_seconds'],
            'measurement_source':str(app/'tools/presentation-measurement'),
            'db_observation_sha256':s['db_bridge']['source_sha256']['db_observation.py'],
            'duration_seconds':paths['db_duration_seconds'],'not_after':launch['window']['not_after']}
        for scenario in ('baseline','rolling'):
            original=result['db-'+scenario+'.json']
            result['db-'+scenario+'.json']={**base,'binding':original['binding']}
        receipt=result['binding-receipt.json'];receipt.update(status='PREPARED_LOCAL_MEASUREMENT_BINDINGS_NOT_RUNTIME',
            missing_paths=[],db_transport='EXISTING_APPROVED_BACKEND_PYTHON_ASYNCMY',
            legacy_mariadb_inputs_required=False,api_calls=0,sql_calls=0)
        target=run/'measurement-bindings';target.mkdir(mode=0o700,exist_ok=True)
        for name,value in result.items():save(target/name,value)
        if a.fixed_comparison_ref:
            # This is a reference to completed fixed measurements, never a synthetic PASS.
            hpa=read(root/'hpa.NOT_ACCEPTED.json')
            registry=read(s['input_registry'])
            if registry.get('capacity_file'):hpa['capacity']=read(registry['capacity_file'])
            need(not (run/'hpa-work/before.json').exists(),'PREPARED_HPA_RUN_MUST_NOT_REBIND')
            binding={k:result['binding-receipt.json']['binding'][k] for k in
                ('cluster_id','namespace_uid','application_uid','normalized_source_sha256')}
            dbconfig={**base,'binding':binding};save(root/'pod-db-hpa.json',dbconfig)
            hpa.update(run_id=launch['run_id']+'-hpa',api=launch['api_url'],origin=read(work/'package/package.json')['origin'],
                binding=binding,window=launch['window'],expected_images=rolling['expected_images'],
                normal_business_ref=a.business_ref,prior_comparison_finished_ref=a.fixed_comparison_ref)
            hpa['db_local_paths']={'transport':'EXISTING_BACKEND_EXEC','schema_credentials_file':str(schema),
                'ca_file':'/run/rds-ca/ca.crt','database_host':launch['database_host']}
            hpa['db_probe_argv']=[sys.executable,'-I',str(HERE/'pod_db_observer.py'),'probe','--config',str(root/'pod-db-hpa.json'),'--execute']
            # Actual capacity remains mandatory. Never accept the template's max10.
            need(not any(v is None for v in hpa['capacity'].values()),'ACTUAL_CAPACITY_AND_ACCOUNT_INPUTS_REQUIRED')
            hpa['max_replicas']=hpa_ceiling(hpa['capacity'],hpa['min_replicas'])
            H=module('closing_handoff_runtime',HERE/'readonly_handoff.py')
            hc=read(s['handoff_settings']);_,argv=H.target_paths(hc,hpa['run_id'],Path(hc['handoff_root'])/('code-'+hc['adopted_ref'][:12]))
            hpa['generator_guard_argv']=argv;H.validate_local(hpa,ops/'tools/hpa-load')
            save(root/'hpa.BOUND.json',hpa)
            path=root/'hpa.NOT_ACCEPTED.json'
            need(not path.is_symlink() and (os.name!='posix' or path.stat().st_uid==os.getuid()),'OWNED_UNPREPARED_HPA_CONFIG_REQUIRED')
            fd,tmp=tempfile.mkstemp(prefix='.bound-hpa-',dir=root)
            with os.fdopen(fd,'w') as f:json.dump(hpa,f,sort_keys=True,separators=(',',':'));f.write('\n')
            os.replace(tmp,path)
            print(json.dumps({'status':'PREPARED_BOUND_HPA_REVIEWED_INPUTS_NOT_EXECUTED',
                'bound_config':str(path),'note':'Use the bound config for control prepare; never rebind an existing HPA run.'}))
        print(json.dumps({'status':'PASS_NEW_PLATFORM_MEASUREMENT_TRANSPORT_BOUND_NO_EXECUTION',
            'binding_directory':str(target),'db_transport':'EXISTING_BACKEND_EXEC','sql_calls':0,'api_calls':0,'recreation_started':False}));return 0
    except Exception as e:
        print(json.dumps({'status':'STOP_RUNTIME_INPUT_BIND','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
            'recreation_started':False,'api_calls':0,'sql_calls':0}));return 2

if __name__=='__main__':raise SystemExit(main())
