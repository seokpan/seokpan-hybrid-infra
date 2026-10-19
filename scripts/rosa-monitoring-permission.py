"""Apply only an exact reviewed monitoring-snapshot IAM increment via Bootstrap State.

No ROSA Plan/Apply, cluster deletion, network, database or protected-input replay.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.parse import unquote

TARGET='aws_iam_role_policy.tf_rosa_monitoring_preservation[0]'
ROLE='seokpan-tf-rosa'
POLICY='seokpan-tf-rosa-monitoring-preservation'
REVIEWED_SAVED_INPUTS={
    'saved.tfplan':'4177dcb72545b9a806c9c981ef8630c29273579fa5d2c4d9d40b8a05c549b340',
    'plan-private.json':'409a969e4c642456e5a92665338be3b22f1616b8fe9b3f911b631c03c3ba395d',
    'monitoring-scope.tfvars.json':'35bed18b1bc163172ac44434a95917403ad0919342359cbd47237d19c894e555'}
SOURCE_SHA256={
    'main.tf':'6cea607aef78a5ddf070d0a59198b8013abb9ccfb208d8c7f645fbccc8b3bfaa',
    'iam.tf':'7ebf420f30b694300fad9843cd04549f2fc713b629a04e988ca374a186be9f7c',
    'backend.tf':'74caaf5cb9d2f9065ee73df7ac85e5f6088396f2496ebbc5de01ebb68fe228de',
    '.terraform.lock.hcl':'767d367bcc4c4ab614647735f7f3e074f59e08ba2632bebf061d4fac35cbb155',
    'rosa_monitoring_preservation.tf':'44e17dfa952c0a6756c913086b75d1d3ad35ea1e5226d77c6dedd494f9b64ee6',
    'hybrid_ec2_iam.tf':'34cf56267d56ae76fcc2067b13ea57d1fc17a7eda078247100bd9dcf03c616b0',
    'hybrid_iam.tf':'f29837e0799b2746e31eef6f71c0590be7218fda9fd465b9ac5b057f0da57f14',
    'rosa_cluster_iam_execution.tf':'c5b4ad4af7a047465a5225955424134e61ec80f9e0f5212976bf768350c2e1d7',
    'rosa_data_connection_permissions.tf':'23c5aa29fb563bd8b7336b2834cdfed9be041290c6eddc4dbd74c9446b955c79',
    'rosa_iam_execution.tf':'c155f0e4823e339be9f3ab7df4399048e8cc4103670cf3c8eb10bf85f730bb91',
    'rosa_plan_read_permissions.tf':'6c1ea74aa8e45d109052cc6af8ce236db1f15900d00991c5b2c1016f25821b75'}

def need(ok,why):
    if not ok:raise RuntimeError(why)

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def private(path):
    p=Path(path);need(p.is_file() and not p.is_symlink(),'PRIVATE_REGULAR_INPUT_REQUIRED')
    if os.name=='posix':
        s=p.stat();need(s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)&0o077==0,'PRIVATE_OWNED_INPUT_REQUIRED')
    need(p.stat().st_size<=33554432,'BOUNDED_INPUT_REQUIRED')
    return p.read_bytes()
def read(path):return json.loads(private(path))
def save(path,value):path.write_bytes(canonical(value));path.chmod(0o600)

def policy_value(raw):
    if isinstance(raw,str):raw=json.loads(unquote(raw))
    need(isinstance(raw,dict) and set(raw)=={'Version','Statement'},'REVIEWED_POLICY_GRAMMAR_REQUIRED')
    rows=[]
    for s in raw['Statement']:
        need(set(s)=={'Sid','Effect','Action','Resource','Condition'},'REVIEWED_STATEMENT_GRAMMAR_REQUIRED')
        value=dict(s)
        for key in ('Action','Resource'):
            value[key]=sorted(s[key] if isinstance(s[key],list) else [s[key]])
        value['Condition']={op:{key:sorted(v if isinstance(v,list) else [v]) for key,v in values.items()}
                            for op,values in s['Condition'].items()}
        rows.append(value)
    need(len({s['Sid'] for s in rows})==len(rows),'UNIQUE_POLICY_STATEMENTS_REQUIRED')
    return {'Version':raw['Version'],'Statement':sorted(rows,key=lambda s:s['Sid'])}

def inline_size(documents):
    size=sum(len(json.dumps(d,ensure_ascii=True,separators=(',',':'))) for d in documents)
    need(size<=10240,'ROSA_INLINE_POLICY_AGGREGATE_LIMIT_EXCEEDED_NO_APPLY')
    return size

def reviewed_refresh(plan):
    """Accept only the observed attachment metadata; never attachment writes."""
    rows=plan.get('resource_drift',[])
    need(len(rows)==3,'EXACT_THREE_REVIEWED_METADATA_DRIFTS_REQUIRED')
    planned={r['address']:r for r in plan.get('resource_changes',[]) if r.get('mode')=='managed'}
    seen=set();kinds=[]
    for row in rows:
        address=row.get('address');change=row.get('change',{})
        b,a=change.get('before'),change.get('after')
        need(address not in seen and not row.get('module_address') and not row.get('previous_address')
             and row.get('mode')=='managed' and change.get('actions')==['update']
             and isinstance(a,dict) and isinstance(b,dict),'REVIEWED_REFRESH_IDENTITY_REQUIRED')
        seen.add(address)
        keys={k for k in set(a)|set(b) if a.get(k)!=b.get(k) or (k in a)!=(k in b)}
        if address in ('aws_iam_policy.tf_foundation_hybrid','aws_iam_policy.tf_foundation_hybrid_ec2'):
            need(row.get('type')=='aws_iam_policy' and keys=={'attachment_count'}
                 and type(a.get('attachment_count')) is int and a['attachment_count']>=0
                 and type(b.get('attachment_count')) is int and b['attachment_count']>=0,
                 'ONLY_REVIEWED_ATTACHMENT_COUNT_REFRESH_ALLOWED')
            kinds.append('POLICY_ATTACHMENT_COUNT')
        else:
            match=re.fullmatch(r'aws_iam_role\.tf\["(bootstrap|foundation|rosa)"\]',address or '')
            need(match is not None
                 and row.get('type')=='aws_iam_role' and keys=={'managed_policy_arns'},
                 'ONLY_REVIEWED_ROLE_ATTACHMENT_METADATA_ALLOWED')
            need(a.get('name')=='seokpan-tf-'+match.group(1),'EXACT_EXISTING_ROLE_METADATA_REQUIRED')
            for values in (b.get('managed_policy_arns'),a.get('managed_policy_arns')):
                need(isinstance(values,list) and len(values)==len(set(values))
                     and all(isinstance(v,str) and re.fullmatch(r'arn:aws:iam::(?:[0-9]{12}|aws):policy/.+',v) for v in values),
                     'POLICY_ARN_METADATA_REQUIRED')
            kinds.append('ROLE_MANAGED_POLICY_METADATA')
        current=planned.get(address,{}).get('change',{})
        need(current.get('actions')==['no-op'] and current.get('before')==a and current.get('after')==a,
             'REFRESHED_RESOURCE_MUST_REMAIN_IDENTICAL_NOOP')
    need(kinds.count('POLICY_ATTACHMENT_COUNT')==2 and kinds.count('ROLE_MANAGED_POLICY_METADATA')==1,
         'EXACT_REVIEWED_REFRESH_SET_REQUIRED')
    return {'policy_attachment_count_refresh':2,'role_attachment_metadata_refresh':1,'attachment_writes':0}

def review(plan,expected,allow_reviewed_refresh=False):
    need(plan.get('complete') is True and not plan.get('errored') and plan.get('applyable') is True,
         'COMPLETE_APPLYABLE_INCREMENT_PLAN_REQUIRED')
    if plan.get('resource_drift'):
        need(allow_reviewed_refresh,'BOOTSTRAP_DRIFT_REQUIRES_OWNER_REVIEW_NO_APPLY')
        reviewed_refresh(plan)
    changed=[]
    for row in plan.get('resource_changes',[]):
        if row.get('mode')=='data':continue
        change=row['change'];actions=change['actions']
        need(not row.get('module_address') and not row.get('previous_address') and not change.get('importing'),
             'MODULE_MOVE_OR_IMPORT_REJECTED')
        if actions==['no-op']:
            need(change.get('before')==change.get('after'),'NONIDENTICAL_BOOTSTRAP_NOOP_REJECTED')
            continue
        need(row['address']==TARGET and row.get('type')=='aws_iam_role_policy'
             and actions==['create'],'UNRELATED_BOOTSTRAP_CHANGE_REJECTED')
        after=change.get('after') or {}
        need(after.get('name')==POLICY and after.get('role')==ROLE,'EXACT_INLINE_POLICY_ROLE_NAME_REQUIRED')
        need(policy_value(after.get('policy'))==policy_value(expected),'INLINE_POLICY_SCOPE_DIFFERS')
        changed.append(row)
    need(len(changed)==1,'EXACT_ONE_INLINE_POLICY_CREATE_REQUIRED')
    need(all(r['actions']==['no-op'] for r in plan.get('output_changes',{}).values()),
         'UNRELATED_BOOTSTRAP_OUTPUT_CHANGE_REJECTED')
    return {'inline_policy_create':1,'managed_update':0,'managed_delete':0,'other_managed_changes':0}

def source_files(repository):
    root=Path(repository)/'terraform/bootstrap'
    names={p.name for p in root.glob('*.tf')}|{p.name for p in root.glob('*.tf.json')}|{'.terraform.lock.hcl'}
    need(names==set(SOURCE_SHA256),'EXACT_PINNED_BOOTSTRAP_FILE_SET_REQUIRED')
    need(not list(root.glob('*.auto.tfvars*')) and not (root/'terraform.tfvars').exists()
         and not (root/'terraform.tfvars.json').exists(),'AUTO_INPUTS_REJECTED')
    for name,expected in SOURCE_SHA256.items():
        p=root/name;need(p.is_file() and not p.is_symlink() and sha(p)==expected,'PINNED_BOOTSTRAP_SOURCE_CHANGED')
    return root

def preflight(receipt_path):
    accepted=read(receipt_path)
    need(accepted.get('status')=='PASS_DENIAL_CLASSIFIED_AND_ENCRYPTED_HELD_RECIPE_PREPARED'
         and accepted.get('classified_objects')==135 and accepted.get('unclassified_objects')==0,'ACCEPTED_RECONSTRUCTION_REQUIRED')
    denied=accepted['snapshot_failure']
    need(denied.get('failed_aws_action')=='ec2:DescribeVolumes'
         and denied.get('authorization_class')=='IDENTITY_POLICY_ALLOW_MISSING'
         and denied.get('this_tool_non_dry_run_snapshot_create_invoked') is False,'CLASSIFIED_ALLOW_MISSING_NO_SNAPSHOT_CREATE_REQUIRED')
    expected=read(accepted['policy_file'])
    need(sha(accepted['policy_file'])==accepted['policy_sha256'],'ACCEPTED_POLICY_HASH_REQUIRED')
    config=read(accepted['lifecycle_config']);manifest=read(config['delete']['manifest'])
    need(config['scope']=='ROSA_ONLY' and manifest['action']=='delete'
         and manifest['cluster_id']==config['source_cluster_id'],'ROSA_SOURCE_BINDING_REQUIRED')
    repo=Path(config['create']['restore_driver']).parent.parent
    source=source_files(repo)
    scope=read(accepted['terraform_scope_inputs'])
    need(set(scope)=={'enable_rosa_monitoring_preservation','rosa_monitoring_preservation_gap_sha256',
        'rosa_monitoring_preservation_sources'} and scope['enable_rosa_monitoring_preservation'] is True
        and re.fullmatch(r'[a-f0-9]{64}',scope['rosa_monitoring_preservation_gap_sha256'])
        and len(scope['rosa_monitoring_preservation_sources'])==4,'FOUR_VOLUME_SCOPE_INPUT_REQUIRED')
    need(not (Path(accepted['recipe_archive']).parent/'snapshot-permission-apply.UNKNOWN.json').exists(),
         'PREVIOUS_UNKNOWN_PERMISSION_APPLY_REQUIRES_RECONCILIATION')
    return accepted,config,manifest,source,expected


def previous_settings(state,rosa_inputs,account):
    """Preserve applied flags from this State; never disable existing IAM by default."""
    resources={(r['type'],r['name']):r for r in state.get('resources',[]) if r.get('mode')=='managed' and not r.get('module')}
    def attributes(kind,name):
        instances=resources.get((kind,name),{}).get('instances',[])
        need(len(instances)<=1,'AMBIGUOUS_EXISTING_BOOTSTRAP_PERMISSION')
        return instances[0]['attributes'] if instances else None
    def document(name):
        a=attributes('aws_iam_role_policy',name)
        return None if a is None else json.loads(a['policy'])
    result={}
    a=attributes('aws_iam_policy','tf_foundation_rosa_iam')
    attachment=attributes('aws_iam_role_policy_attachment','tf_foundation_rosa_iam')
    need((a is not None)==(attachment is not None),'PARTIAL_EXISTING_ACCOUNT_PERMISSION_REQUIRES_REVIEW')
    result['enable_rosa_account_iam_permissions']=a is not None
    cluster=document('tf_rosa_cluster_iam')
    result['enable_rosa_cluster_iam_permissions']=cluster is not None
    if cluster:
        prefix='arn:aws:iam::'+account+':policy/seokpan-fnd-rosa-openshift-'
        arns=set()
        for s in cluster['Statement']:
            for values in s.get('Condition',{}).values():
                value=values.get('iam:PolicyARN',[])
                arns.update(x for x in (value if isinstance(value,list) else [value]) if x.startswith(prefix))
        need(len(arns)==6,'EXACT_EXISTING_SIX_OPERATOR_POLICIES_REQUIRED')
        result['rosa_cluster_operator_policy_arns']={x.split(':policy/',1)[1]:x for x in sorted(arns)}
    reader=document('tf_rosa_first_plan_read')
    result['enable_rosa_plan_read_permissions']=reader is not None
    if reader:
        vpcs=re.findall(r'arn:aws:ec2:ap-northeast-2:'+account+r':vpc/(vpc-[a-f0-9]+)',json.dumps(reader))
        need(len(set(vpcs))==1 and vpcs[0]==rosa_inputs['foundation']['vpc_id'],'EXISTING_VPC_READ_SCOPE_DIFFERS')
        result['rosa_plan_foundation_vpc_id']=vpcs[0]
    data=document('tf_rosa_data_connection')
    result['enable_rosa_data_connection_permissions']=data is not None
    if data:
        ids=set(re.findall(r'arn:aws:ec2:ap-northeast-2:'+account+r':security-group/(sg-[a-f0-9]+)',json.dumps(data)))
        supplied=rosa_inputs['foundation']['data_security_groups']
        need(set(supplied)=={'mariadb','redis'} and ids==set(supplied.values()) and len(ids)==2,
             'EXISTING_DATA_PERMISSION_SCOPE_DIFFERS')
        result['rosa_data_security_group_ids']={'rds':supplied['mariadb'],'redis':supplied['redis']}
    return result

class Run:
    def __init__(self,out):
        self.out=out;self.count=0
        self.env={k:v for k,v in os.environ.items() if not k.startswith(('TF_CLI_ARGS','TF_VAR_','TF_LOG','RHCS_'))
                  and k not in ('AWS_PROFILE','AWS_DEFAULT_PROFILE','AWS_ROLE_ARN','AWS_WEB_IDENTITY_TOKEN_FILE','TF_CLI_CONFIG_FILE')}
        self.env.update(AWS_REGION='ap-northeast-2',AWS_DEFAULT_REGION='ap-northeast-2',AWS_MAX_ATTEMPTS='1',AWS_PAGER='',
                        TF_WORKSPACE='default',TF_IN_AUTOMATION='1',CHECKPOINT_DISABLE='1')
    def command(self,args,codes=(0,)):
        self.count+=1
        try:p=subprocess.run(args,capture_output=True,env=self.env,timeout=1800)
        except subprocess.TimeoutExpired:raise RuntimeError('COMMAND_OUTCOME_UNKNOWN_NO_RETRY')
        (self.out/('command-'+str(self.count)+'.private.log')).write_bytes(p.stdout+p.stderr)
        need(p.returncode in codes,'COMMAND_FAILED_PRIVATE_LOG_NO_RETRY')
        return p
    def aws(self,args,missing=False):
        p=self.command(['aws','--region','ap-northeast-2','--output','json',*args],codes=(0,254) if missing else (0,))
        if p.returncode:
            need(missing and b'NoSuchEntity' in p.stderr,'IAM_READ_FAILED_NO_FALLBACK')
            return None
        return json.loads(p.stdout)
    def role_policy(self):
        value=self.aws(['iam','get-role-policy','--role-name',ROLE,'--policy-name',POLICY],missing=True)
        return None if value is None else value['PolicyDocument']

def resume_saved(a,out,accepted,manifest,expected,run,aggregate):
    """Reuse the one inspected plan; keep source, State and exact-policy guards."""
    old=Path(a.resume_permission_from).resolve()
    need(old!=out and old.is_dir() and not Path(a.resume_permission_from).is_symlink(),'EXISTING_PERMISSION_RUN_REQUIRED')
    receipt=read(old/'receipt.json')
    need(receipt.get('reason')=='BOOTSTRAP_DRIFT_REQUIRES_OWNER_REVIEW_NO_APPLY','REVIEWED_DRIFT_STOP_REQUIRED')
    for name,value in REVIEWED_SAVED_INPUTS.items():
        private(old/name);need(sha(old/name)==value,'INSPECTED_PLAN_OR_VARIABLE_HASH_CHANGED')
    target=source_files(old/'source')
    before=read(old/'before.tfstate')
    need(before.get('serial')==17 and isinstance(before.get('lineage'),str),'INSPECTED_PRIOR_STATE_REQUIRED')
    backend=read(old/'tf-data/terraform.tfstate')['backend']
    need(backend['type']=='s3' and backend['config']['key']=='phase2/bootstrap/terraform.tfstate'
         and backend['config']['bucket']=='seokpan-tfstate-'+manifest['account_id']
         and backend['config']['region']=='ap-northeast-2','BOOTSTRAP_BACKEND_ONLY')
    run.env['TF_DATA_DIR']=str(old/'tf-data')
    tf=['terraform','-chdir='+str(target)]
    need(json.loads(run.command(tf+['version','-json']).stdout).get('terraform_version')=='1.16.4',
         'ADOPTED_TERRAFORM_1_16_4_REQUIRED')
    plan=read(old/'plan-private.json');saved=old/'saved.tfplan'
    rendered=json.loads(run.command(tf+['show','-json',str(saved)]).stdout)
    need(rendered==plan,'SAVED_BINARY_AND_INSPECTED_JSON_DIFFER')
    reviewed=review(plan,expected,allow_reviewed_refresh=True)
    result={'status':'PASS_EXACT_MONITORING_SNAPSHOT_POLICY_PLAN_NOT_APPLIED','review':reviewed,
        'refresh_review':reviewed_refresh(plan),'plan_sha256':sha(saved),'policy_sha256':accepted['policy_sha256'],
        'scope':'MONITORING_SNAPSHOTS_ONLY','resumed_from':str(old),'bootstrap_plan_repeated':False,
        'rosa_plan_repeated':False,'iam_apply_attempted':False,'deletion_started':False,'deletion_ready':False,
        'rosa_inline_policy_aggregate_candidate_characters':aggregate}
    save(out/'plan-review.json',result)
    if not a.apply_exact_policy:return result
    current=json.loads(run.command(tf+['state','pull']).stdout)
    need((before['lineage'],before['serial'])==(current.get('lineage'),current.get('serial')),
         'BOOTSTRAP_STATE_CHANGED_NO_APPLY')
    need(run.role_policy() is None,'INLINE_POLICY_CREATED_CONCURRENTLY_NO_OVERWRITE')
    for name,value in REVIEWED_SAVED_INPUTS.items():need(sha(old/name)==value,'INSPECTED_PLAN_OR_VARIABLE_HASH_CHANGED')
    source_files(old/'source')
    marker=Path(a.reconstruction_receipt).parent/'snapshot-permission-apply.NO_AUTOMATIC_RETRY.json'
    with marker.open('x') as f:json.dump({'plan_sha256':sha(saved),'result_directory':str(out)},f)
    run.command(tf+['apply','-input=false','-no-color','-lock-timeout=60s',str(saved)])
    need(policy_value(run.role_policy())==policy_value(expected),'APPLIED_POLICY_READBACK_DIFFERS_NO_REAPPLY')
    result.update(status='PASS_EXACT_MONITORING_SNAPSHOT_POLICY_APPLIED_AND_READBACK',
                  iam_apply_attempted=True,accepted_policy_reused=False)
    save(Path(a.reconstruction_receipt).parent/'monitoring-policy-accepted.json',result)
    return result

def execute(a,out):
    accepted,c,m,source,expected=preflight(a.reconstruction_receipt)
    run=Run(out)
    caller=run.aws(['sts','get-caller-identity'])
    need(caller.get('Account')==m['account_id'] and ':assumed-role/seokpan-tf-bootstrap/' in caller.get('Arn',''),
         'EXISTING_BOOTSTRAP_PURPOSE_ROLE_REQUIRED')
    cache=Path(a.reconstruction_receipt).parent/'monitoring-policy-accepted.json'
    if cache.is_file():
        old=read(cache)
        need(old.get('status')=='PASS_EXACT_MONITORING_SNAPSHOT_POLICY_APPLIED_AND_READBACK'
             and old.get('policy_sha256')==accepted['policy_sha256']
             and policy_value(run.role_policy())==policy_value(expected),'ACCEPTED_POLICY_CHANGED_NO_REPLAN')
        return {**old,'accepted_policy_reused':True,'bootstrap_plan_repeated':False,'iam_apply_repeated':False}
    marker=Path(a.reconstruction_receipt).parent/'snapshot-permission-apply.NO_AUTOMATIC_RETRY.json'
    need(not marker.exists(),'PREVIOUS_PERMISSION_APPLY_REQUIRES_RECONCILIATION')
    need(run.role_policy() is None,'EXISTING_UNMANAGED_INLINE_POLICY_NO_TAKEOVER')
    names=run.aws(['iam','list-role-policies','--role-name',ROLE]).get('PolicyNames')
    need(isinstance(names,list) and len(names)<=32 and len(set(names))==len(names),'BOUNDED_INLINE_POLICY_INVENTORY_REQUIRED')
    policies=[]
    for name in names:
        value=run.aws(['iam','get-role-policy','--role-name',ROLE,'--policy-name',name])['PolicyDocument']
        policies.append(json.loads(unquote(value)) if isinstance(value,str) else value)
    aggregate=inline_size(policies+[expected])
    if getattr(a,'resume_permission_from',None):
        return resume_saved(a,out,accepted,m,expected,run,aggregate)
    # Use an isolated copy of pinned source; no init or edits in another owner's workspace.
    target=out/'source/terraform/bootstrap';target.mkdir(parents=True,mode=0o700)
    for name in SOURCE_SHA256:(target/name).write_bytes((source/name).read_bytes())
    inputs=out/'monitoring-scope.tfvars.json';inputs.write_bytes(private(accepted['terraform_scope_inputs']));inputs.chmod(0o600)
    input_sha=sha(inputs)
    run.env['TF_DATA_DIR']=str(out/'tf-data')
    tf=['terraform','-chdir='+str(target)]
    version=json.loads(run.command(tf+['version','-json']).stdout)
    need(version.get('terraform_version')=='1.16.4','ADOPTED_TERRAFORM_1_16_4_REQUIRED')
    run.command(tf+['init','-input=false','-no-color','-lockfile=readonly'])
    backend=read(out/'tf-data/terraform.tfstate')['backend']
    need(backend['type']=='s3' and backend['config']['key']=='phase2/bootstrap/terraform.tfstate'
         and backend['config']['bucket']=='seokpan-tfstate-'+m['account_id']
         and backend['config']['region']=='ap-northeast-2','BOOTSTRAP_BACKEND_ONLY')
    before=json.loads(run.command(tf+['state','pull']).stdout);save(out/'before.tfstate',before)
    legacy=previous_settings(before,read(c['create']['var_file']),m['account_id'])
    settings=read(inputs);need(not (set(settings)&set(legacy)),'NEW_SCOPE_MUST_NOT_OVERRIDE_EXISTING_FLAGS')
    settings.update(legacy);save(inputs,settings);input_sha=sha(inputs)
    saved=out/'saved.tfplan'
    run.command(tf+['plan','-input=false','-no-color','-lock-timeout=60s','-detailed-exitcode',
        '-var-file='+str(inputs),'-out='+str(saved)],codes=(0,2))
    plan=json.loads(run.command(tf+['show','-json',str(saved)]).stdout);save(out/'plan-private.json',plan)
    reviewed=review(plan,expected)
    result={'status':'PASS_EXACT_MONITORING_SNAPSHOT_POLICY_PLAN_NOT_APPLIED','review':reviewed,
            'plan_sha256':sha(saved),'policy_sha256':accepted['policy_sha256'],'scope':'MONITORING_SNAPSHOTS_ONLY',
            'rosa_plan_repeated':False,'iam_apply_attempted':False,'deletion_started':False,'deletion_ready':False}
    result['existing_permission_flags_preserved']={k:v for k,v in legacy.items() if k.startswith('enable_')}
    result['rosa_inline_policy_aggregate_candidate_characters']=aggregate
    save(out/'plan-review.json',result)
    if not a.apply_exact_policy:return result
    need(sha(saved)==result['plan_sha256'] and sha(inputs)==input_sha
         and all(sha(target/name)==v for name,v in SOURCE_SHA256.items()),'PLAN_SOURCE_OR_SCOPE_CHANGED')
    current=json.loads(run.command(tf+['state','pull']).stdout)
    need((before['lineage'],before['serial'])==(current['lineage'],current['serial']),'BOOTSTRAP_STATE_CHANGED_NO_APPLY')
    need(run.role_policy() is None,'INLINE_POLICY_CREATED_CONCURRENTLY_NO_OVERWRITE')
    with marker.open('x') as f:json.dump({'plan_sha256':sha(saved),'result_directory':str(out)},f)
    run.command(tf+['apply','-input=false','-no-color','-lock-timeout=60s',str(saved)])
    need(policy_value(run.role_policy())==policy_value(expected),'APPLIED_POLICY_READBACK_DIFFERS_NO_REAPPLY')
    result.update(status='PASS_EXACT_MONITORING_SNAPSHOT_POLICY_APPLIED_AND_READBACK',iam_apply_attempted=True,
                  accepted_policy_reused=False,bootstrap_plan_repeated=False)
    save(cache,result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reconstruction-receipt',required=True)
    p.add_argument('--output-parent',required=True)
    p.add_argument('--apply-exact-policy',action='store_true')
    p.add_argument('--resume-permission-from')
    a=p.parse_args();os.umask(0o077)
    out=Path(tempfile.mkdtemp(prefix='rosa-monitoring-permission-',dir=a.output_parent))
    try:result=execute(a,out)
    except Exception as e:result={'status':'STOP_MONITORING_PERMISSION','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
        'automatic_retry':False,'deletion_started':False,'deletion_ready':False}
    save(out/'receipt.json',result);print(json.dumps(result,indent=2));print('Private permission result directory: '+str(out))
    return 0 if result['status'].startswith('PASS_') else 1

if __name__=='__main__':raise SystemExit(main())
