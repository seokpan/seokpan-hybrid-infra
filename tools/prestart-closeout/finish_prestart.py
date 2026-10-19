"""Prepare all pre-create links from existing files. Never create, Sync, SQL or accept unknowns."""
import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent

def need(ok,code):
    if not ok:raise RuntimeError(code)

def read(path,maximum=16777216):
    p=Path(path)
    need(p.is_file() and not p.is_symlink() and p.stat().st_size<=maximum,'BOUNDED_REGULAR_EXISTING_INPUT_REQUIRED')
    if os.name=='posix':need(p.stat().st_uid==os.getuid(),'OWNED_INPUT_REQUIRED')
    return json.loads(p.read_text(encoding='utf-8-sig'))

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def requirement_phases(issues):
    # Actual new-node capacity cannot be a pre-create input. Existing HPA bind/
    # activation guards still require current DB/account/node evidence at execution.
    later='CURRENT_DB_ACCOUNT_LIMITS_RESERVATION_AND_POOL_EVIDENCE_SOURCE_REQUIRED'
    return ([x for x in issues if x!=later],
            ['ACTUAL_DB_ACCOUNT_LIMITS_AND_NEW_NODE_CAPACITY_BEFORE_HPA'] if later in issues else [])

def write(path,value):
    path=Path(path);raw=(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    if path.exists():
        need(not path.is_symlink() and (os.name!='posix' or path.stat().st_uid==os.getuid()),'OWNED_OUTPUT_REQUIRED')
        if path.read_bytes()==raw:return
    fd,tmp=tempfile.mkstemp(prefix='.prestart-',dir=path.parent)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def one_existing(paths,predicate=None):
    # Distinct copies with identical bytes are one input; different values need review.
    found={}
    for raw in paths:
        if not raw:continue
        p=Path(raw).expanduser()
        if not p.is_file() or p.is_symlink():continue
        try:
            value=read(p)
            if predicate is None or predicate(value):found.setdefault(digest(p),str(p))
        except (ValueError,RuntimeError,UnicodeError):continue
    return next(iter(found.values())) if len(found)==1 else None


def persist_candidate(path,value):
    # Re-running metadata collection must not erase a subsequently bound live config.
    provenance=path.parent/(path.name+'.generated-sha256.json')
    if path.exists() and read(path)!=value:
        need(provenance.is_file() and read(provenance).get('sha256')==digest(path),
             'EXISTING_BOUND_OR_DIFFERENT_CONFIG_PRESERVED')
    write(path,value);write(provenance,{'sha256':digest(path)})

def flatten(value,prefix=''):
    if isinstance(value,dict):return [p for k,v in value.items() for p in flatten(v,prefix+'.'+k if prefix else k)]
    if value is None or value=='':return [prefix]
    return []


def discover(root,skip):
    result={};count=0
    for folder,dirs,names in os.walk(root,followlinks=False):
        path=Path(folder);depth=len(path.relative_to(root).parts)
        dirs[:]=[d for d in dirs if d not in {'.git','node_modules','providers','__pycache__','source','repository',
            'app-measurement','hpa-cutover','measurement-bindings'} and not (path/d).is_symlink()
            and (path/d).resolve()!=skip.resolve() and depth<2]
        for name in names:
            if not name.endswith('.json') or '.sops.' in name or 'TEMPLATE' in name:continue
            p=path/name
            if p.stat().st_size>1048576:continue
            count+=1;need(count<=1500,'BOUNDED_SOURCE_DISCOVERY_LIMIT_REVIEW_EXACT_REGISTRY')
            try:x=read(p,1048576)
            except Exception:continue
            if not isinstance(x,dict):continue
            kinds=[]
            if x.get('status')=='SOURCE_GIT_CONTROL_CONTRACT_COLLECTED':kinds.append('source_contract')
            if x.get('status')=='SOURCE_LOCAL_READ_COMPLETED':kinds.append('source_read')
            if 'categories' in x and 'inventory_complete_confirmed_by_operator' in x:kinds.append('writer_plan')
            if 'budget' in x and 'window' in x and x.get('budget',{}).get('estimate_ref') and not flatten(x['window']):kinds.append('cost')
            if {'vpc_id','network_subnets','rosa_account_role_arns','rosa_operator_policy_arns','rds_security_group_id','redis_security_group_id'}<=set(x):kinds.append('foundation_handoff')
            if x.get('schema')==1 and set(x)=={'schema','database_url'}:kinds.append('schema_credentials')
            if x.get('source_cluster_id') and 'foundation_handoff' in x and 'recreate_inputs_sha256' in x:kinds.append('recreate_readiness')
            for k in kinds:result.setdefault(k+'_candidates',[]).append(str(p))
    return result,count


def combine_readiness(ops,out,lifecycle,variables,slots,cost_valid):
    f=slots.get('foundation_handoff',{}).get('path');p=slots.get('recreate_readiness',{}).get('path')
    if not (f and p):return {'status':'PENDING_CURRENT_FOUNDATION_AND_NETWORK_PROOF','missing':['CURRENT_FOUNDATION_AND_NETWORK_PROOF']}
    R=module('closing_current_input_gate',ops/'scripts/rosa-recreate-readiness.py')
    proof=copy.deepcopy(read(p));handoff=read(f);candidate=copy.deepcopy(lifecycle);inputs=copy.deepcopy(variables)
    # Convert actual current outputs, not historical SG/role identifiers.
    projection=R.project_handoff(handoff)
    inputs['foundation'].update(projection)
    revision=proof['foundation_handoff'].get('revision')
    need(revision,'CURRENT_FOUNDATION_REVISION_REQUIRED')
    inputs['foundation']['revision']=revision
    old_manifest=read(lifecycle['delete']['manifest']);old_inputs=read(old_manifest['var_file'])
    changed_ids=R.differences(R.project_inputs(old_inputs['foundation']),R.project_inputs(inputs['foundation']))
    if changed_ids:
        return {'status':'STOP_CURRENT_FOUNDATION_SCOPE_DIFF','missing':['CURRENT_FOUNDATION_RESOURCE_DIFF_REQUIRES_SCOPE_REVIEW'],
            'difference_paths':changed_ids,'create_invoked':False,'original_config_changed':False}
    inputs.setdefault('execution_review',{})['foundation_revision']=revision
    for key,input_key in [('review_reference','cost_review'),('window_reference','window')]:
        if proof.get('budget',{}).get(key):inputs['execution_review'][input_key]=proof['budget'][key]
    var=out/'recreate-inputs.CURRENT.NOT_EXECUTED.json';persist_candidate(var,inputs)
    proof['recreate_inputs_sha256']=digest(var)
    proof['foundation_handoff']['file']=f;proof['foundation_handoff']['sha256']=digest(f)
    candidate['create']['var_file']=str(var)
    candidate['create']['network_ready']=proof.get('network',{}).get('ready') is True
    candidate['create']['budget_accepted']=bool(cost_valid and proof.get('budget',{}).get('accepted') is True)
    pending=bool(list(Path(lifecycle['evidence_root']).glob('rosa-create-*/apply-started.NO_AUTOMATIC_RETRY.json')))
    result=R.evaluate(candidate,inputs,digest(var),proof,handoff,dt.datetime.now(dt.timezone.utc),dt.date(2026,10,19),pending)
    proof_path=out/'recreate-readiness.CURRENT.NOT_EXECUTED.json';persist_candidate(proof_path,proof)
    config_path=out/'lifecycle.CURRENT.NOT_EXECUTED.json';persist_candidate(config_path,candidate)
    result.update(config_file=str(config_path),readiness_file=str(proof_path),original_config_changed=False,
        output_provenance={'foundation_sha256':digest(f),'network_proof_sha256':digest(p)},create_invoked=False)
    return result

def prepare(c):
    os.umask(0o077)
    root=Path(c['root']);run=Path(c['run']);out=Path(c['out'])
    need(root.is_dir() and run.is_dir() and not root.is_symlink() and not run.is_symlink(),'EXISTING_RUN_REQUIRED')
    need(not (run/'hpa-work/before.json').exists() and not (run/'cloud-ready/binding.json').exists(),
         'EXISTING_LIVE_RUN_USE_STAGE_RESUME_NOT_PRECREATE_REBIND')
    out.mkdir(mode=0o700,exist_ok=True)
    need(not out.is_symlink(),'REGULAR_OUTPUT_DIRECTORY_REQUIRED')
    issues=[];files={};commands={}
    for name in ('app_source','ops_source'):
        need(Path(c[name]).is_dir(),'EXISTING_ADOPTED_SOURCE_REQUIRED')
    app=Path(c['app_source']);ops=Path(c['ops_source'])
    lifecycle=read(c['lifecycle_config']);variables=read(lifecycle['create']['var_file'])
    registry=read(c['input_registry']) if Path(c['input_registry']).is_file() else {}
    detected,scanned=discover(root,out)
    for key,values in detected.items():
        if not registry.get(key):registry[key]=values
    write(out/'discovered-source-locations.json',{'candidates':detected,'files_considered':scanned,'raw_contents_exported':False})
    launch_path=one_existing(c['launch_candidates'],lambda x:x.get('schema')==1 and 'database_host' in x)
    need(launch_path is not None,'UNIQUE_EXISTING_LAUNCH_INPUT_REQUIRED')
    launch=read(launch_path)
    # Derive endpoints only from the accepted configuration, never from source DB files.
    need(launch.get('database_host') and launch.get('redis_host'),'ACCEPTED_DATA_ENDPOINTS_REQUIRED')
    launch['infra_sha']=c['adopted_app_ref']
    launch['release_review_ref']=c['release_review_ref']
    protected={}
    try:
        M=module('closeout_materialize',HERE/'materialize_inputs.py')
        protected=M.materialize(c['protected_sources'],out/'protected',launch['database_host'])
    except Exception as error:
        issues.append(str(error) if type(error) is RuntimeError else 'PROTECTED_INPUT_MATERIALIZATION_FAILED')
    for key in ('secret_revision',):
        if protected.get(key):launch[key]=protected[key]
        if registry.get(key):launch[key]=registry[key]
    # Current cost/time are supplied by existing evidence, not silently accepted estimates.
    cost_file=one_existing(registry.get('cost_candidates',[]),lambda x:'budget' in x and 'window' in x)
    cost_valid=False
    if cost_file:
        cost=read(cost_file);launch['budget']=cost['budget'];launch['window']=cost['window']
        try:
            L=module('closeout_launch',app/'tools/cloud-launch/cloud_launch.py');L.budget_check(launch);cost_valid=True
        except Exception:issues.append('CURRENT_COST_OR_WINDOW_CONTRACT_INVALID')
    else:issues.append('CURRENT_COST_ALLOWANCES_AND_START_END_SOURCE_REQUIRED')
    if not launch.get('secret_revision'):issues.append('PROTECTED_INPUT_LOGICAL_REVISION_REQUIRED')
    persist_candidate(out/'launch-inputs.NOT_ACCEPTED.json',launch);files['launch_inputs']=str(out/'launch-inputs.NOT_ACCEPTED.json')
    # Prepare the B-only DB bridge. Only metadata crosses the existing SSH route now.
    db=copy.deepcopy(c['db_bridge']);db['database_host']=launch['database_host']
    db['hpa_source']=str(ops/'tools/hpa-load');db['measurement_source']=str(app/'tools/presentation-measurement')
    for key in ('peer','paths','remote_python','ssh_prefix'):
        if registry.get('db',{}).get(key) is not None:db[key]=registry['db'][key]
    write(out/'db-settings.json',db)
    bridge=module('closeout_db_bridge',HERE/'db_bridge.py')
    try:
        metadata=bridge.invoke(db,bridge.packet(db,'metadata'))
        db_ok=bool(metadata.get('mariadb') and metadata.get('credential_private_owned_readable')
                   and metadata.get('ca_readable_regular'))
    except Exception as error:
        metadata={'status':'PENDING_EXISTING_DB_HOST_METADATA','reason':str(error) if type(error) is RuntimeError else type(error).__name__,
                  'sql_calls':0,'credential_values_exported':False};db_ok=False
    write(out/'db-metadata-receipt.json',metadata)
    paths=copy.deepcopy(c['execution_paths'])
    if db_ok:
        paths.update(db_mariadb=metadata['mariadb'],db_credential_file=db['paths']['credential_file'],
            db_ca_file=db['paths']['ca_file'],db_target_reference=db['reference'])
    write(out/'execution-paths.NOT_ACCEPTED.json',paths)
    files['execution_paths']=str(out/'execution-paths.NOT_ACCEPTED.json')
    # Locate the source artifacts; do not request a new report or accept stale network state.
    slots={}
    for name,row in c['input_slots'].items():
        candidates=registry.get(name+'_candidates',[])+row.get('candidates',[])
        if protected.get(name):candidates=[protected[name]]+candidates
        value=one_existing(candidates)
        slots[name]={'path':value,'stage':row['stage'],'sha256':digest(value) if value else None}
        if not value and row['stage']=='BEFORE_CREATE':issues.append(name.upper()+'_EXISTING_SOURCE_REQUIRED')
    foundation=slots.get('foundation_handoff',{}).get('path')
    try:
        recreation=combine_readiness(ops,out,lifecycle,variables,slots,cost_valid)
        if recreation['status']!='PASS_LOCAL_RECREATE_DISPATCH_READY_NOT_EXECUTED':
            issues.extend(recreation.get('missing',['CURRENT_FOUNDATION_OUTPUT_AND_READINESS_CONTRACT_REQUIRED']))
        write(out/'recreate-binding-receipt.json',recreation)
    except Exception:
        recreation={};issues.append('FOUNDATION_OUTPUT_FORMAT_OR_CURRENT_READINESS_REQUIRED')
    oc=shutil.which('oc');kustomize=shutil.which('kustomize')
    if not kustomize and oc:
        wrapper=out/'kustomize'
        script='#!'+sys.executable+'\nimport os,sys\na=sys.argv[1:]\nif not a or a[0]!="build":raise SystemExit("STOP_EXPECTED_BUILD")\nos.execv('+repr(oc)+',['+repr(oc)+',"kustomize",*a[1:]])\n'
        if wrapper.exists():need(wrapper.read_text()==script,'EXISTING_RENDER_WRAPPER_PRESERVED')
        else:wrapper.write_text(script);wrapper.chmod(0o700)
        kustomize=str(wrapper)
    for key,value in [('oc',oc),('kustomize',kustomize)]:
        if not value:issues.append('EXISTING_'+key.upper()+'_EXECUTABLE_REQUIRED')
    pre=read(app/'tools/cloud-prestart/prestart.TEMPLATE.NOT_ACCEPTED.json')
    pre.update(lifecycle_config=recreation.get('config_file',c['lifecycle_config']),launch_inputs=files['launch_inputs'],
        final_data_handoff=slots['final_data_handoff']['path'] or str(out/'final-data-handoff.AFTER_DATA_EXECUTION.json'),
        schema_credentials_file=slots['schema_credentials']['path'],runtime_inputs_file=slots['runtime_inputs']['path'],
        gitops_source=registry.get('gitops_source'),kustomize=kustomize,oc=oc,
        personal_kubeconfig=str(run/'personal.kubeconfig'))
    if not pre['gitops_source']:pre['gitops_source']=c.get('packaged_gitops_source')
    if not pre['gitops_source'] or not Path(pre['gitops_source']).is_dir():issues.append('EXISTING_PINNED_GITOPS_CHECKOUT_PATH_REQUIRED')
    if importlib.util.find_spec('yaml') is None:issues.append('EXISTING_PYTHON_PYYAML_IMPORT_REQUIRED')
    persist_candidate(out/'prestart.NOT_ACCEPTED.json',pre);files['prestart_config']=str(out/'prestart.NOT_ACCEPTED.json')
    handoff=module('closeout_handoff',HERE/'readonly_handoff.py')
    try:
        base,code=handoff.setup(read(c['handoff_settings']))
        share={'status':'PASS_READ_ONLY_CODE_AND_ACL_INSTALLED_NO_CURRENT_RUN','root':str(base),'code':str(code),'reader':c['reader']}
    except Exception as error:
        share={'status':'PENDING_READ_ONLY_HANDOFF_SETUP','reason':str(error) if type(error) is RuntimeError else type(error).__name__}
        issues.append('READ_ONLY_ACL_OR_EXISTING_READER_ACCOUNT_REQUIRED')
    write(out/'handoff-setup-receipt.json',share)
    try:
        transport=module('closing_metadata_transport',HERE/'metadata_transport.py').install(Path(c['transport_settings']))
    except Exception as error:
        transport={'status':'PENDING_TASK_METADATA_TRANSPORT','reason':str(error) if type(error) is RuntimeError else type(error).__name__}
        issues.append('TASK_METADATA_TRANSPORT_INSTALLATION_RECONCILIATION_REQUIRED')
    write(out/'metadata-transport-receipt.json',transport)
    # The DB argv is complete before a cluster exists. Actual binding is supplied later.
    hpa=read(ops/'tools/hpa-load/config.TEMPLATE.NOT_ACCEPTED.json')
    hpa.update(oc=oc,personal_kubeconfig=str(run/'personal.kubeconfig'))
    hpa['db_probe_argv']=[sys.executable,'-I',str(HERE/'pod_db_observer.py'),'probe',
        '--config',str(out/'pod-db-hpa.json'),'--execute']
    hpa['db_local_paths']={'transport':'EXISTING_BACKEND_EXEC','schema_credentials_file':protected.get('schema_credentials'),
        'ca_file':'/run/rds-ca/ca.crt','database_host':launch['database_host']}
    # No copying 38/68, max10 or old node capacities as current accepted numbers.
    if registry.get('capacity_file'):
        hpa['capacity']=read(registry['capacity_file']);hpa['max_replicas']=registry.get('max_replicas',hpa['max_replicas'])
    else:issues.append('CURRENT_DB_ACCOUNT_LIMITS_RESERVATION_AND_POOL_EVIDENCE_SOURCE_REQUIRED')
    persist_candidate(out/'hpa.NOT_ACCEPTED.json',hpa)
    write(out/'input-source-map.json',slots)
    write(out/'selected-input-registry.json',registry)
    for action in ('bind','publish'):
        commands['handoff-'+action]=[sys.executable,'-I',str(HERE/'readonly_handoff.py'),action,'--settings',c['handoff_settings']]
    commands['db-metadata']=[sys.executable,'-I',str(HERE/'db_bridge.py'),'metadata','--settings',str(out/'db-settings.json')]
    write(out/'commands.json',commands)
    environment={'B_PRESTART_CLOSEOUT':HERE,'B_READY_CONFIG':out/'prestart.NOT_ACCEPTED.json',
        'B_EXECUTION_PATHS':out/'execution-paths.NOT_ACCEPTED.json','B_HPA_CONFIG':out/'hpa.NOT_ACCEPTED.json',
        'B_DB_SETTINGS':out/'db-settings.json','B_HANDOFF_SETTINGS':Path(c['handoff_settings'])}
    if recreation.get('config_file'):environment['B_LIFECYCLE_CONFIG']=Path(recreation['config_file'])
    if recreation.get('readiness_file'):environment['B_RECREATE_READINESS']=Path(recreation['readiness_file'])
    shell=''.join('export '+k+'='+shlex.quote(str(v))+'\n' for k,v in environment.items())
    shell_path=out/'session-closeout.sh'
    if shell_path.exists():need(shell_path.read_text()==shell,'EXISTING_CLOSEOUT_SESSION_PRESERVED')
    else:
        fd=os.open(shell_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:f.write(shell)
    # Windows observer paths remain host-specific. Never run them on the Controller.
    issues,capacity_pending=requirement_phases(issues)
    result={'status':'PASS_PRECREATE_LINKS_PREPARED_NOT_RECREATED' if not issues else 'PENDING_EXACT_PRECREATE_INPUTS',
        'remaining_before_create':sorted(set(issues)),
        'after_create_only':['NEW_CLUSTER_ID_API_INGRESS_WORKER_BINDING','NEW_NAMESPACE_APPLICATION_AND_PROTECTED_UIDS',
            'NEW_OAUTH_CALLBACK_PERSONAL_LOGIN','LIVE_NODE_CAPACITY_AND_APP_READY_BUSINESS','FINAL_IMPORT_SQL_SCHEMA_AND_ROTATION_AT_EXECUTION',
            'SELECTED_MEASUREMENT_RUNTIME_RESULTS'],
        'prepared_files':files,'legacy_db_path_metadata_confirmed':db_ok,
        'primary_db_transport':'EXISTING_BACKEND_EXEC_PRIVATE_STDIN_NO_NEW_INSTALL',
        'primary_db_runtime_verified':False,'handoff_setup':share['status'],
        'metadata_transport_setup':transport['status'],
        'remaining_selected_test_inputs':capacity_pending+[k for k,row in slots.items() if row['stage']=='BEFORE_SELECTED_MAIL_TEST' and not row['path']],
        'source_registry':c['input_registry'],'output_directory':str(out),
        'cloud_calls':0,'sql_calls':0,'recreation_started':False,'completed_tests_repeated':False,
        'remote_db_credentials_copied':False,'unknown_values_accepted':False}
    result['necessary_protected_input_extraction']=protected.get('status','PENDING')
    result['protected_input_decrypt_invocations']=protected.get('sops_decrypt_invocations',0)
    write(out/'receipt.json',result)
    print(json.dumps(result,indent=2));return 0 if not issues else 2

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--settings',type=Path,required=True);a=p.parse_args()
    try:return prepare(read(a.settings))
    except Exception as e:
        print(json.dumps({'status':'STOP_PRESTART_CLOSEOUT','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
            'recreation_started':False,'sql_calls':0,'cloud_calls':0}));return 2

if __name__=='__main__':raise SystemExit(main())
