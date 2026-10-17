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
SOURCE_SHA256={
    'main.tf':'6cea607aef78a5ddf070d0a59198b8013abb9ccfb208d8c7f645fbccc8b3bfaa',
    'iam.tf':'7ebf420f30b694300fad9843cd04549f2fc713b629a04e988ca374a186be9f7c',
    'backend.tf':'74caaf5cb9d2f9065ee73df7ac85e5f6088396f2496ebbc5de01ebb68fe228de',
    '.terraform.lock.hcl':'767d367bcc4c4ab614647735f7f3e074f59e08ba2632bebf061d4fac35cbb155',
    'rosa_monitoring_preservation.tf':'44e17dfa952c0a6756c913086b75d1d3ad35ea1e5226d77c6dedd494f9b64ee6'}

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

def review(plan,expected):
    need(plan.get('complete') is True and not plan.get('errored') and plan.get('applyable') is True,
         'COMPLETE_APPLYABLE_INCREMENT_PLAN_REQUIRED')
    need(not plan.get('resource_drift'),'BOOTSTRAP_DRIFT_REQUIRES_OWNER_REVIEW_NO_APPLY')
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
    # Use an isolated copy of pinned source; no init or edits in another owner's workspace.
    target=out/'source/terraform/bootstrap';target.mkdir(parents=True,mode=0o700)
    for name in SOURCE_SHA256:(target/name).write_bytes((source/name).read_bytes())
    inputs=out/'monitoring-scope.tfvars.json';inputs.write_bytes(private(accepted['terraform_scope_inputs']))
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
    saved=out/'saved.tfplan'
    run.command(tf+['plan','-input=false','-no-color','-lock-timeout=60s','-detailed-exitcode',
        '-var-file='+str(inputs),'-out='+str(saved)],codes=(0,2))
    plan=json.loads(run.command(tf+['show','-json',str(saved)]).stdout);save(out/'plan-private.json',plan)
    reviewed=review(plan,expected)
    result={'status':'PASS_EXACT_MONITORING_SNAPSHOT_POLICY_PLAN_NOT_APPLIED','review':reviewed,
            'plan_sha256':sha(saved),'policy_sha256':accepted['policy_sha256'],'scope':'MONITORING_SNAPSHOTS_ONLY',
            'rosa_plan_repeated':False,'iam_apply_attempted':False,'deletion_started':False,'deletion_ready':False}
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
    a=p.parse_args();os.umask(0o077)
    out=Path(tempfile.mkdtemp(prefix='rosa-monitoring-permission-',dir=a.output_parent))
    try:result=execute(a,out)
    except Exception as e:result={'status':'STOP_MONITORING_PERMISSION','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
        'automatic_retry':False,'deletion_started':False,'deletion_ready':False}
    save(out/'receipt.json',result);print(json.dumps(result,indent=2));print('Private permission result directory: '+str(out))
    return 0 if result['status'].startswith('PASS_') else 1

if __name__=='__main__':raise SystemExit(main())
