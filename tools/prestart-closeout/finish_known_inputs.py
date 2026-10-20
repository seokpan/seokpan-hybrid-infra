"""Reconcile one observed task-key failure and bind established references; no cloud/SQL/decrypt."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile

HERE=Path(__file__).resolve().parent
def need(ok,code):
    if not ok:raise RuntimeError(code)
def raw(path,private=True):
    p=Path(path);fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and not p.is_symlink() and s.st_size<=2097152,'REGULAR_BOUNDED_INPUT_REQUIRED')
        if private and os.name=='posix':need(s.st_uid==os.getuid() and not s.st_mode&0o077,'PRIVATE_OWNED_INPUT_REQUIRED')
        return f.read(2097153)
def read(path,private=True):return json.loads(raw(path,private).decode('utf-8-sig'))
def digest(data):return hashlib.sha256(data.replace(b'\r\n',b'\n')).hexdigest()
def exact(path,data):
    p=Path(path)
    if p.exists():need(raw(p)==data,'EXISTING_DIFFERENT_FILE_PRESERVED');return
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def replace(path,data):
    p=Path(path)
    if p.exists():raw(p)
    fd,tmp=tempfile.mkstemp(prefix='.known-input-',dir=p.parent)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)
def encoded(value):return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()

def payload():
    lines=(HERE/'repair-SHA256SUMS').read_text(encoding='ascii').splitlines();rows={}
    for line in lines:
        wanted,name=line.split();need(re.fullmatch(r'[a-f0-9]{64}',wanted) and '/' not in name and '\\' not in name,'EXACT_REPAIR_MANIFEST_REQUIRED')
        rows[name]=wanted
    need(set(rows)=={'finish_known_inputs.py','finish_prestart.py','metadata_transport.py','restricted_delegate.py',
        'observer_access_plan.py'},'EXACT_REPAIR_MANIFEST_REQUIRED')
    for name,wanted in rows.items():need(hashlib.sha256(raw(HERE/name)).hexdigest()==wanted,'REPAIR_PAYLOAD_HASH_MISMATCH')
    return rows

def reconcile(c,a):
    run=Path(c['run']);base=Path(c['out']).parent;out=Path(c['out']);work=out/'p05-reconcile'
    need(not (run/'hpa-work/before.json').exists() and not (run/'cloud-ready/binding.json').exists(),'LIVE_RUN_MUST_NOT_REBIND')
    work.mkdir(mode=0o700,exist_ok=True);need(not work.is_symlink(),'OWNED_REPAIR_DIRECTORY_REQUIRED')
    if (work/'receipt.json').exists():return read(work/'receipt.json')
    need(not (work/'STARTED.NO_AUTOMATIC_RETRY.json').exists(),'PARTIAL_P05_RECONCILIATION_READ_MARKER_NO_RETRY')
    need(not (base/'original-dispatch.json').exists(),'EXISTING_TRANSPORT_INSTALL_REQUIRES_RECONCILIATION')
    failure=read(out/'metadata-transport-receipt.json')
    need(failure.get('reason')=='EXACT_ORIGINAL_RESTRICTED_DISPATCH_REQUIRED','EXACT_OBSERVED_FAILURE_REQUIRED')
    dispatcher=run/'generator-link/restricted_dispatch.py';old=raw(dispatcher)
    need(digest(old)==a.observed_dispatch_sha256,'OBSERVED_DISPATCH_CHANGED_NO_RETRY')
    old_module=raw(base/'metadata_transport.py')
    need(digest(old_module)==a.old_transport_sha256,'INSTALLED_TRANSPORT_CHANGED_NO_RETRY')
    old_prepare=raw(base/'finish_prestart.py')
    need(digest(old_prepare)==a.old_preparation_sha256,'INSTALLED_PREPARATION_CHANGED_NO_RETRY')
    settings=Path(c['transport_settings']);before=raw(settings);transport=json.loads(before)
    keyfile=Path.home()/'.ssh/authorized_keys';keys=raw(keyfile).decode('utf-8')
    matching=[line for line in keys.splitlines() if transport['public_key'] in line]
    need(len(matching)==1,'EXACT_EXISTING_TASK_KEY_REQUIRED')
    forced=re.search(r'command="([^"]+)"',matching[0]);need(forced is not None,'EXISTING_FORCED_COMMAND_REQUIRED')
    need(shlex.split(forced.group(1))==[transport['python'],'-I',str(dispatcher)],'EXACT_EXISTING_FORCED_COMMAND_REQUIRED')
    rows=payload();delegate=base/'restricted_delegate.py'
    need(not delegate.exists(),'EXISTING_DELEGATE_REQUIRES_RECONCILIATION')
    new=dict(transport)
    new['original_dispatch_sha256']=a.observed_dispatch_sha256
    new['original_dispatch_sha256_variants']=[rows['restricted_delegate.py']]
    new['reviewed_delegate']={'argv':[transport['python'],'-I',str(delegate)],'sha256':rows['restricted_delegate.py']}
    exact(work/'transport-settings.before.json',before);exact(work/'metadata_transport.before.py',old_module)
    exact(work/'finish_prestart.before.py',old_prepare)
    exact(work/'observed-dispatch.before.py',old)
    marker={'phase':'BEFORE_LOCAL_FILE_CHANGE','old_dispatch_sha256':a.observed_dispatch_sha256,
        'old_module_sha256':a.old_transport_sha256,'new_module_sha256':rows['metadata_transport.py'],
        'new_delegate_sha256':rows['restricted_delegate.py'],'automatic_retry':False}
    exact(work/'STARTED.NO_AUTOMATIC_RETRY.json',encoded(marker))
    exact(delegate,raw(HERE/'restricted_delegate.py'));replace(base/'metadata_transport.py',raw(HERE/'metadata_transport.py'))
    replace(base/'finish_prestart.py',raw(HERE/'finish_prestart.py'))
    exact(base/'observer_access_plan.py',raw(HERE/'observer_access_plan.py'))
    replace(settings,encoded(new))
    spec=importlib.util.spec_from_file_location('known_transport',base/'metadata_transport.py')
    T=importlib.util.module_from_spec(spec);spec.loader.exec_module(T)
    result=T.install(settings)
    need(result.get('status')=='PASS_TASK_METADATA_TRANSPORT_INSTALLED','TRANSPORT_INSTALL_NOT_CONFIRMED')
    prior=read(base/'original-dispatch.json')
    need(prior['argv']==new['reviewed_delegate']['argv'] and digest(raw(dispatcher))==a.observed_dispatch_sha256,
         'DELEGATE_OR_PRESERVED_ORIGINAL_MISMATCH')
    after=raw(keyfile).decode('utf-8');remaining=lambda text:[line for line in text.splitlines() if transport['public_key'] not in line]
    need(remaining(keys)==remaining(after),'UNRELATED_KEYS_CHANGED')
    need(shlex.join([transport['python'],'-I',str(base/'metadata_transport.py'),'--settings',str(settings)]) in after,
         'FORCED_TRANSPORT_READBACK_REQUIRED')
    result.update(status='PASS_P05_SCOPED_TRANSPORT_RECONCILED_NOT_SSH_RUNTIME',
        observed_original_preserved=True,unknown_original_executed=False,
        reviewed_two_command_delegate=True,unrelated_keys_changed=False,
        old_probe_repeated=False,rolling_started=False,acl_or_decrypt_repeated=False)
    replace(out/'metadata-transport-receipt.json',encoded(result))
    main=read(out/'receipt.json');main['metadata_transport_setup']=result['status']
    main['remaining_before_create']=[x for x in main.get('remaining_before_create',[]) if x!='TASK_METADATA_TRANSPORT_INSTALLATION_RECONCILIATION_REQUIRED']
    main['p05_reconciliation_receipt']=str(work/'receipt.json');replace(out/'receipt.json',encoded(main))
    exact(work/'receipt.json',encoded(result));return result

def bind_references(c,a):
    out=Path(c['out']);work=out/'known-inputs';work.mkdir(mode=0o700,exist_ok=True)
    value=raw(a.foundation_file,False)
    need(hashlib.sha256(value).hexdigest()==a.foundation_sha256,'ACCEPTED_FOUNDATION_REFERENCE_CHANGED')
    registry=Path(c['input_registry']);before=raw(registry);r=json.loads(before)
    exact(work/'registry.before.json',before)
    existing=r.get('foundation_handoff_candidates',[])
    need(not existing or existing==[a.foundation_file],'OTHER_FOUNDATION_CANDIDATES_PRESERVED')
    r['foundation_handoff_candidates']=[a.foundation_file]
    reference={'kind':'LAST_ACCEPTED_REFERENCE_NOT_CURRENT_RUNTIME','global_limit':a.db_limit,
        'global_user_limit':a.db_user_limit,'reserved':a.reserved,'evidence_ref':a.db_reference,
        'account_limits':{'identity_svc':None,'game_svc':None},'node_capacity':None,
        'current_limit_read':'FIRST_APPROVED_DATA_CONNECTION_BEFORE_APP_CAPACITY_ACCEPTANCE',
        'current_values_asserted':False,
        'global_process_privilege_required':True,'existing_grant_reference_does_not_prove_global_process':True,
        'privilege_plan_generator':str(Path(c['out']).parent/'observer_access_plan.py'),
        'permission_input':'existing protected Data-admin execution path or current PROCESS grant proof',
        'grant_execution':'only if missing, using actual CURRENT_USER host binding; never grant application accounts'}
    exact(work/'db-reference.NOT_ACCEPTED.json',encoded(reference));r['capacity_reference_file']=str(work/'db-reference.NOT_ACCEPTED.json')
    origin={'host':a.source_host,'execution':'HOST_LOCAL_EXISTING_CONTEXT','source_contract':a.source_contract,
        'source_read_directory':a.source_read_directory,'controller_local':False,
        'current_source_or_writer_stop_asserted':False}
    exact(work/'source-origin.json',encoded(origin));r['source_execution_origin_file']=str(work/'source-origin.json')
    reference_meta={'path':a.foundation_file,'sha256':a.foundation_sha256,'current_network_asserted':False,
        'historical_accepted_reference_only':True}
    exact(work/'foundation-reference.json',encoded(reference_meta));r['foundation_reference_review']=str(work/'foundation-reference.json')
    launch_path=out/'launch-inputs.NOT_ACCEPTED.json';launch_before=raw(launch_path);launch=json.loads(launch_before)
    basis=read(a.cost_reference)
    fields={'reported_spend_usd','unreported_and_retained_allowance_usd','hourly_upper_usd',
            'shutdown_reserve_hours','variable_allowance_usd','estimate_ref'}
    need(set(basis.get('budget',{}))==fields and basis.get('source'),'EXISTING_PROTECTED_COST_BASIS_REQUIRED')
    need(all(basis['budget'][k] is not None for k in fields),'COST_BASIS_VALUES_REQUIRED_NO_ZERO_FILL')
    exact(work/'cost-planning-reference.NOT_ACCEPTED.json',encoded(basis))
    exact(work/'launch-inputs.before.json',launch_before)
    filled=[]
    for key,value in basis['budget'].items():
        if launch.setdefault('budget',{}).get(key) in (None,''):
            launch['budget'][key]=value;filled.append('budget.'+key)
    replace(launch_path,encoded(launch));r['cost_planning_reference_file']=str(work/'cost-planning-reference.NOT_ACCEPTED.json')
    required=('reported_spend_usd','unreported_and_retained_allowance_usd','hourly_upper_usd',
              'shutdown_reserve_hours','variable_allowance_usd','estimate_ref')
    missing=['budget.'+k for k in required if launch.get('budget',{}).get(k) in (None,'')]
    missing+=['window.'+k for k in ('not_before','not_after') if launch.get('window',{}).get(k) in (None,'')]
    cost={'existing_launch_file':str(out/'launch-inputs.NOT_ACCEPTED.json'),'missing_fields':missing,
          'existing_spend_currentness':'NOT_ASSERTED','time_window_prepared_by':'B_AT_ACTUAL_START',
          'unknown_values_filled_with_zero':False,'planning_fields_filled':filled,
          'basis_ref':basis['source'],'budget_accepted':False}
    exact(work/'cost-fields.json',encoded(cost));replace(registry,encoded(r))
    source_map=out/'input-source-map.json'
    if source_map.exists():
        mapped=read(source_map);prior=mapped.get('foundation_handoff',{})
        need(prior.get('path') in (None,a.foundation_file),'DIFFERENT_BOUND_FOUNDATION_PRESERVED')
        mapped['foundation_handoff']={**prior,'path':a.foundation_file,'sha256':a.foundation_sha256,
            'historical_accepted_reference_only':True,'current_network_asserted':False}
        replace(source_map,encoded(mapped))
    main_path=out/'receipt.json'
    if main_path.exists():
        main=read(main_path)
        later='CURRENT_DB_ACCOUNT_LIMITS_RESERVATION_AND_POOL_EVIDENCE_SOURCE_REQUIRED'
        main['remaining_before_create']=[x for x in main.get('remaining_before_create',[])
            if x not in {'FOUNDATION_HANDOFF_EXISTING_SOURCE_REQUIRED',later,
                         'CURRENT_COST_ALLOWANCES_AND_START_END_SOURCE_REQUIRED'}]
        for label in ('B_EXECUTION_WINDOW_NOT_ASSIGNED','B_BUDGET_BASIS_REVIEW_AT_START'):
            if label not in main['remaining_before_create']:main['remaining_before_create'].append(label)
        selected=main.setdefault('remaining_selected_test_inputs',[])
        if 'ACTUAL_DB_ACCOUNT_LIMITS_AND_NEW_NODE_CAPACITY_BEFORE_HPA' not in selected:
            selected.append('ACTUAL_DB_ACCOUNT_LIMITS_AND_NEW_NODE_CAPACITY_BEFORE_HPA')
        main['known_reference_binding']=str(work)
        main['current_network_or_cost_auto_accepted']=False
        replace(main_path,encoded(main))
    return {'P06':'PASS_EXISTING_REFERENCE_PATH_BOUND_CURRENT_NETWORK_PENDING',
        'P07':'PASS_DB_REFERENCE_AND_EXISTING_OBSERVER_BOUND_NOT_CURRENT_LIMIT_ACCEPTANCE',
        'P08':'PASS_SOURCE_HOST_LOCATION_BOUND_WRITER_CONTROL_FACTS_PENDING',
        'P09':'PASS_EXISTING_LEDGER_AND_B_PLANNING_FIELDS_BOUND_START_WINDOW_HELD',
        'cost_missing_fields':missing,'reference_directory':str(work)}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--settings',type=Path,required=True)
    p.add_argument('--observed-dispatch-sha256',required=True);p.add_argument('--old-transport-sha256',required=True)
    p.add_argument('--old-preparation-sha256',required=True)
    p.add_argument('--foundation-file',required=True);p.add_argument('--foundation-sha256',required=True)
    p.add_argument('--db-limit',required=True,type=int);p.add_argument('--db-user-limit',required=True,type=int)
    p.add_argument('--reserved',required=True,type=int);p.add_argument('--db-reference',required=True)
    p.add_argument('--source-host',required=True);p.add_argument('--source-contract',required=True);p.add_argument('--source-read-directory',required=True)
    p.add_argument('--cost-reference',type=Path,required=True)
    p.add_argument('--execute-local-repair',action='store_true');a=p.parse_args();os.umask(0o077)
    try:
        need(a.execute_local_repair,'EXPLICIT_LOCAL_REPAIR_REQUIRED');need(os.name=='posix','CONTROLLER_LINUX_REQUIRED')
        c=read(a.settings);result={'P05':reconcile(c,a),**bind_references(c,a),
            'status':'P05_RECONCILED_AND_KNOWN_REFERENCES_BOUND_NOT_CREATE_READY',
            'remaining_original_facts':['CURRENT_FOUNDATION_NETWORK_STATE','EXISTING_ADMIN_EXECUTION_PATH_OR_PROCESS_PROOF',
                'EXTERNAL_WRITER_CONTROL_FACTS','SMTP_SOURCE_ONLY_IF_MAIL_SELECTED'],
            'b_owned_before_actual_start':['SOURCE_HOST_METADATA_BINDING','EXECUTION_WINDOW_AND_UPDATED_COST_RECONCILIATION'],
            'cloud_calls':0,'sql_calls':0,'decrypt_invocations':0,'acl_repeated':False,'recreation_started':False}
        replace(Path(c['out'])/'known-inputs-receipt.json',encoded(result));print(json.dumps(result,indent=2));return 0
    except Exception as e:
        print(json.dumps({'status':'STOP_KNOWN_INPUT_REPAIR','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
            'automatic_retry':False,'cloud_calls':0,'sql_calls':0,'recreation_started':False}));return 2

if __name__=='__main__':raise SystemExit(main())
