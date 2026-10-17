"""Exact managed restoration policy and Bootstrap ownership; no volume creation or deletion."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote

HERE=Path(__file__).resolve().parent
def module(name,file,expected):
    raw=Path(file).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise RuntimeError('PINNED_DEPENDENCY_REQUIRED')
    spec=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
M=module('restore_permission_base',HERE/'rosa-monitoring-permission.py','49d4ccca1e7106baa97c8eb06a7d84e4ede246340d0daacd64dacae43a3661b2')
need=M.need
NAME='seokpan-tf-rosa-monitoring-restore'
TARGET='aws_iam_policy.tf_rosa_monitoring_restore[0]'
ATTACH='aws_iam_role_policy_attachment.tf_rosa_monitoring_restore[0]'
BOOT='aws_iam_role_policy.tf_bootstrap'
NEW_HCL_SHA='59bf0d2250beb038ed644653f4703597e04822d63f9953f40d0a608fc4cc7f43'
MAP_SHA='8b6d36243ba7223e0b3c78281c65b5bbb704097fa1ad728105699bc21e6f189e'
MANAGE=['iam:CreatePolicy','iam:DeletePolicy','iam:GetPolicy','iam:GetPolicyVersion','iam:ListPolicyVersions',
        'iam:CreatePolicyVersion','iam:DeletePolicyVersion','iam:TagPolicy','iam:UntagPolicy','iam:ListPolicyTags']
OLD_SOURCE='''  source_policy_documents = (
    var.enable_rosa_account_iam_permissions
    ? [data.aws_iam_policy_document.tf_bootstrap_rosa_iam.json]
    : []
  )'''
NEW_SOURCE='''  source_policy_documents = concat(
    var.enable_rosa_account_iam_permissions ? [data.aws_iam_policy_document.tf_bootstrap_rosa_iam.json] : [],
    var.enable_rosa_monitoring_restore ? [data.aws_iam_policy_document.tf_bootstrap_monitoring_restore[0].json] : []
  )'''

def doc(raw):
    if isinstance(raw,str):raw=json.loads(unquote(raw))
    value=copy.deepcopy(raw)
    for s in value['Statement']:
        for k in ('Action','Resource','NotAction','NotResource'):
            if k in s:s[k]=sorted(s[k] if isinstance(s[k],list) else [s[k]])
        for conditions in s.get('Condition',{}).values():
            for k,v in conditions.items():conditions[k]=sorted(v if isinstance(v,list) else [v])
    value['Statement']=sorted(value['Statement'],key=lambda s:M.canonical(s))
    return value

def extras(account):
    arn='arn:aws:iam::'+account+':policy/'+NAME
    return [
        {'Sid':'ManageExactRosaMonitoringRestorePolicy','Effect':'Allow','Action':MANAGE,'Resource':[arn]},
        {'Sid':'AttachExactRosaMonitoringRestorePolicy','Effect':'Allow','Action':['iam:AttachRolePolicy','iam:DetachRolePolicy'],
         'Resource':['arn:aws:iam::'+account+':role/seokpan-tf-rosa'],'Condition':{'ArnEquals':{'iam:PolicyARN':[arn]}}}]

def augment(before,account):
    result=doc(before);sids=[s.get('Sid') for s in result['Statement']]
    need(not any(s['Sid'] in sids for s in extras(account)),'MANAGEMENT_POLICY_ALREADY_PRESENT_RECONCILE')
    result['Statement']+=extras(account);return doc(result)

def build_policy(scope):
    account=scope['account_id'];rows=scope['volumes'];keys=scope['kms_keys']
    need(re.fullmatch(r'[0-9]{12}',account) and scope.get('region')=='ap-northeast-2'
         and len(rows)==4 and len({r['snapshot_id'] for r in rows})==4
         and set(keys)=={r['source_kms_key'] for r in rows},'EXACT_PROTECTED_RESTORE_SCOPE_REQUIRED')
    need(all(r.get('snapshot_state')=='completed' and r.get('namespace')=='openshift-monitoring'
             and re.fullmatch(r'snap-[0-9a-f]+',r['snapshot_id']) for r in rows),'COMPLETED_MONITORING_SNAPSHOTS_ONLY')
    need(keys and all(re.fullmatch(r'arn:aws:kms:ap-northeast-2:'+account+r':key/[a-zA-Z0-9-]+',k) for k in keys),
         'SAME_ACCOUNT_EXACT_KMS_KEYS_REQUIRED')
    snapshots=sorted('arn:aws:ec2:ap-northeast-2::snapshot/'+r['snapshot_id'] for r in rows)
    volume='arn:aws:ec2:ap-northeast-2:'+account+':volume/*'
    service={'StringEquals':{'kms:ViaService':'ec2.ap-northeast-2.amazonaws.com'}}
    policy={'Version':'2012-10-17','Statement':[
        {'Sid':'UseExactSnapshots','Effect':'Allow','Action':['ec2:CreateVolume'],'Resource':snapshots},
        {'Sid':'CreateOnlyEncryptedSnapshotRestorations','Effect':'Allow','Action':['ec2:CreateVolume'],'Resource':[volume],
         'Condition':{'ArnEquals':{'ec2:ParentSnapshot':snapshots},'Bool':{'ec2:Encrypted':'true'},
                      'Null':{'aws:RequestTag/seokpan-restored-cluster':'false','aws:RequestTag/seokpan-source-snapshot':'false'}}},
        {'Sid':'TagOnlyDuringVolumeCreation','Effect':'Allow','Action':['ec2:CreateTags'],'Resource':[volume],
         'Condition':{'StringEquals':{'ec2:CreateAction':'CreateVolume'}}},
        {'Sid':'ReadExactKeyMetadata','Effect':'Allow','Action':['kms:DescribeKey'],'Resource':sorted(keys)},
        {'Sid':'UseExactKeysThroughEc2','Effect':'Allow','Action':['kms:Decrypt','kms:GenerateDataKeyWithoutPlaintext','kms:ReEncrypt*'],
         'Resource':sorted(keys),'Condition':service},
        {'Sid':'ServiceResourceGrantsOnly','Effect':'Allow','Action':['kms:CreateGrant'],'Resource':sorted(keys),
         'Condition':{**service,'Bool':{'kms:GrantIsForAWSResource':'true'}}}]}
    need(len(M.canonical(policy))<=6144,'MANAGED_RESTORE_POLICY_SIZE_LIMIT')
    return policy

def refresh_review(plan,snapshot_policy):
    planned={r['address']:r for r in plan.get('resource_changes',[]) if r.get('mode')=='managed'}
    for r in plan.get('resource_drift',[]):
        b,a=r['change'].get('before'),r['change'].get('after');address=r['address']
        need(isinstance(a,dict) and isinstance(b,dict) and r['change']['actions']==['update'],'UNKNOWN_DRIFT_REJECTED')
        changed={k for k in set(a)|set(b) if a.get(k)!=b.get(k) or (k in a)!=(k in b)}
        if address in ('aws_iam_policy.tf_foundation_hybrid','aws_iam_policy.tf_foundation_hybrid_ec2'):
            need(changed=={'attachment_count'},'POLICY_DOCUMENT_DRIFT_REJECTED')
        else:
            match=re.fullmatch(r'aws_iam_role\.tf\["(bootstrap|foundation|rosa)"\]',address)
            need(match and a.get('name')=='seokpan-tf-'+match.group(1)
                 and changed and changed<={'managed_policy_arns','inline_policy'},'UNRELATED_ROLE_DRIFT_REJECTED')
            if 'inline_policy' in changed:
                def named(values):return {v['name']:doc(v['policy']) for v in (values or [])}
                prior,current=named(b.get('inline_policy')),named(a.get('inline_policy'))
                need(all(current.get(k)==v for k,v in prior.items())
                     and set(current)-set(prior)<={M.POLICY}
                     and all(current[k]==doc(snapshot_policy) for k in set(current)-set(prior)),
                     'ONLY_ACCEPTED_SNAPSHOT_POLICY_METADATA_ADDITION')
        c=planned.get(address,{}).get('change',{})
        need(c.get('actions')==['no-op'] and c.get('before')==a and c.get('after')==a,'REFRESH_MUST_REMAIN_IDENTICAL_NOOP')

def review(plan,expected,account,snapshot_policy):
    need(plan.get('complete') is True and plan.get('applyable') is True and not plan.get('errored'),'COMPLETE_RESTORE_PERMISSION_PLAN_REQUIRED')
    refresh_review(plan,snapshot_policy)
    counts={};boot_after=None;arn='arn:aws:iam::'+account+':policy/'+NAME
    for r in plan.get('resource_changes',[]):
        if r.get('mode')=='data':continue
        c=r['change'];a=c.get('after') or {};b=c.get('before') or {};address=r['address']
        need(not r.get('module_address') and not r.get('previous_address') and not c.get('importing'),'NO_MODULE_MOVE_IMPORT')
        if c['actions']==['no-op']:
            need(c.get('before')==c.get('after'),'CHANGED_NOOP_REJECTED');continue
        if address==BOOT:
            need(c['actions']==['update'] and a.get('name')==b.get('name')=='seokpan-tf-bootstrap'
                 and a.get('role')==b.get('role')=='seokpan-tf-bootstrap'
                 and {k:v for k,v in a.items() if k!='policy'}=={k:v for k,v in b.items() if k!='policy'}
                 and doc(a['policy'])==augment(b['policy'],account),'EXACT_BOOTSTRAP_MANAGEMENT_INCREMENT_ONLY')
            boot_after=doc(a['policy'])
        elif address==TARGET:
            need(c['actions']==['create'] and a.get('name')==NAME and a.get('path')=='/'
                 and doc(a['policy'])==doc(expected),'EXACT_MANAGED_RESTORE_POLICY_CREATE_ONLY')
        elif address==ATTACH:
            need(c['actions']==['create'] and a.get('role')=='seokpan-tf-rosa' and a.get('policy_arn')==arn,
                 'EXACT_RESTORE_POLICY_ATTACHMENT_ONLY')
        else:raise RuntimeError('UNRELATED_BOOTSTRAP_CHANGE_REJECTED')
        need(address not in counts,'DUPLICATE_CHANGE');counts[address]=1
    need(set(counts)=={BOOT,TARGET,ATTACH} and all(v['actions']==['no-op'] for v in plan.get('output_changes',{}).values()),
         'EXACT_THREE_RESTORE_PERMISSION_CHANGES_REQUIRED')
    return boot_after

def inputs(a):
    accepted=M.read(a.reconstruction_receipt);c=M.read(accepted['lifecycle_config']);manifest=M.read(c['delete']['manifest'])
    need(c.get('scope')=='ROSA_ONLY' and c['restoration'].get('snapshot_map_sha256')==MAP_SHA
         and M.sha(c['restoration']['snapshot_map'])==MAP_SHA,'COMPLETED_ACCEPTED_MAP_REQUIRED')
    need(M.sha(accepted['policy_file'])==accepted['policy_sha256'],'ACCEPTED_SNAPSHOT_POLICY_HASH_REQUIRED')
    scope=M.read(a.scope_inputs)
    journal=M.read(Path(c['restoration']['snapshot_map']).parent/'snapshot-journal.json')
    need(scope['volumes']==journal['volumes'] and scope['account_id']==manifest['account_id']==journal['account_id']
         and scope['source_cluster_id']==manifest['cluster_id']==journal['cluster_id'],'SCOPE_AND_ACCEPTED_JOURNAL_BINDING_REQUIRED')
    expected=build_policy(scope);return accepted,c,manifest,scope,expected

def apply_policy(a,out):
    accepted,c,manifest,scope,expected=inputs(a);run=M.Run(out);account=manifest['account_id']
    caller=run.aws(['sts','get-caller-identity'])
    need(caller.get('Account')==account and ':assumed-role/seokpan-tf-bootstrap/' in caller.get('Arn',''),'BOOTSTRAP_PURPOSE_ROLE_REQUIRED')
    arn='arn:aws:iam::'+account+':policy/'+NAME;cache=Path(a.reconstruction_receipt).parent/'restore-policy-accepted.json'
    if cache.exists():
        old=M.read(cache);need(old.get('policy_sha256')==hashlib.sha256(M.canonical(expected)).hexdigest(),'ACCEPTED_RESTORE_SCOPE_CHANGED')
        current=run.aws(['iam','get-policy','--policy-arn',arn])['Policy']
        observed=run.aws(['iam','get-policy-version','--policy-arn',arn,'--version-id',current['DefaultVersionId']])['PolicyVersion']['Document']
        attached=run.aws(['iam','list-attached-role-policies','--role-name','seokpan-tf-rosa'])['AttachedPolicies']
        need(doc(observed)==doc(expected) and sum(r['PolicyArn']==arn for r in attached)==1,'ACCEPTED_RESTORE_POLICY_CHANGED')
        return {**old,'accepted_policy_reused':True}
    marker=cache.with_name('restore-policy-apply.NO_AUTOMATIC_RETRY.json');need(not marker.exists(),'PREVIOUS_RESTORE_PERMISSION_APPLY_REQUIRES_RECONCILIATION')
    probe=run.aws(['iam','get-policy','--policy-arn',arn],missing=True);need(probe is None,'EXISTING_UNMANAGED_RESTORE_POLICY_NO_TAKEOVER')
    attached=run.aws(['iam','list-attached-role-policies','--role-name','seokpan-tf-rosa'])['AttachedPolicies']
    need(len(attached)<10,'RESTORE_ROLE_ATTACHMENT_QUOTA_REVIEW_REQUIRED')
    old=Path(a.snapshot_permission_run);original=M.source_files(old/'source')
    need(M.sha(old/'monitoring-scope.tfvars.json')==M.REVIEWED_SAVED_INPUTS['monitoring-scope.tfvars.json'],'ACCEPTED_EXISTING_FLAGS_REQUIRED')
    need(M.sha(a.restore_hcl)==NEW_HCL_SHA,'PINNED_RESTORE_HCL_REQUIRED')
    target=out/'source/terraform/bootstrap';target.mkdir(parents=True,mode=0o700)
    for name in M.SOURCE_SHA256:(target/name).write_bytes((original/name).read_bytes())
    text=(target/'iam.tf').read_text();need(text.count(OLD_SOURCE)==1,'EXACT_BOOTSTRAP_DOCUMENT_SOURCE_REQUIRED')
    (target/'iam.tf').write_text(text.replace(OLD_SOURCE,NEW_SOURCE),encoding='utf-8')
    (target/'rosa_monitoring_restore.tf').write_bytes(Path(a.restore_hcl).read_bytes())
    pinned={p.name:M.sha(p) for p in target.iterdir() if p.is_file()}
    run.env['TF_DATA_DIR']=str(out/'tf-data');tf=['terraform','-chdir='+str(target)]
    need(json.loads(run.command(tf+['version','-json']).stdout)['terraform_version']=='1.16.4','ADOPTED_TERRAFORM_REQUIRED')
    run.command(tf+['init','-input=false','-no-color','-lockfile=readonly'])
    backend=M.read(out/'tf-data/terraform.tfstate')['backend']
    need(backend['type']=='s3' and backend['config']['key']=='phase2/bootstrap/terraform.tfstate'
         and backend['config']['bucket']=='seokpan-tfstate-'+account and backend['config']['region']=='ap-northeast-2','BOOTSTRAP_BACKEND_ONLY')
    before=json.loads(run.command(tf+['state','pull']).stdout);M.save(out/'before.tfstate',before)
    values=M.read(old/'monitoring-scope.tfvars.json')
    values.update(M.previous_settings(before,M.read(c['create']['var_file']),account))
    values.update(enable_rosa_monitoring_restore=True,rosa_monitoring_restore_policy_json=M.canonical(expected).decode())
    varfile=out/'restore-scope.tfvars.json';M.save(varfile,values);varsha=M.sha(varfile)
    saved=out/'saved.tfplan';run.command(tf+['plan','-input=false','-no-color','-lock-timeout=60s','-detailed-exitcode','-var-file='+str(varfile),'-out='+str(saved)],codes=(0,2))
    plan=json.loads(run.command(tf+['show','-json',str(saved)]).stdout);M.save(out/'plan-private.json',plan)
    snapshot_expected=M.read(accepted['policy_file']);boot_after=review(plan,expected,account,snapshot_expected)
    boot_names=run.aws(['iam','list-role-policies','--role-name','seokpan-tf-bootstrap'])['PolicyNames'];documents=[]
    for name in boot_names:
        raw=run.aws(['iam','get-role-policy','--role-name','seokpan-tf-bootstrap','--policy-name',name])['PolicyDocument']
        documents.append(boot_after if name=='seokpan-tf-bootstrap' else (json.loads(unquote(raw)) if isinstance(raw,str) else raw))
    need('seokpan-tf-bootstrap' in boot_names,'MANAGED_BOOTSTRAP_POLICY_REQUIRED');size=M.inline_size(documents)
    result={'status':'PASS_EXACT_RESTORE_PERMISSION_PLAN_NOT_APPLIED','managed_policy_create':1,'role_attachment_create':1,
        'bootstrap_inline_update':1,'other_resource_changes':0,'policy_sha256':hashlib.sha256(M.canonical(expected)).hexdigest(),
        'plan_sha256':M.sha(saved),'bootstrap_inline_candidate_characters':size,'scope_snapshot_count':4,'scope_kms_keys':len(scope['kms_keys']),
        'rosa_inline_increase':0,'deletion_ready':False,'new_volumes_created':0}
    result['monitoring_volume_cutoffs_utc']=sorted(str(r.get('snapshot_start_time','NOT_REPORTED')) for r in scope['volumes'])
    result['monitoring_writes_after_cutoff']='NOT_IN_SAVED_SNAPSHOTS'
    M.save(out/'plan-review.json',result)
    if not a.apply_exact:return result
    current=json.loads(run.command(tf+['state','pull']).stdout)
    need((current['lineage'],current['serial'])==(before['lineage'],before['serial']) and M.sha(varfile)==varsha
         and M.sha(saved)==result['plan_sha256'] and all(M.sha(target/n)==v for n,v in pinned.items()),'STATE_SOURCE_OR_INPUT_CHANGED_NO_APPLY')
    need(run.aws(['iam','get-policy','--policy-arn',arn],missing=True) is None,'RESTORE_POLICY_CREATED_CONCURRENTLY')
    with marker.open('x') as f:json.dump({'plan_sha256':result['plan_sha256'],'result_directory':str(out)},f)
    run.command(tf+['apply','-input=false','-no-color','-lock-timeout=60s',str(saved)])
    observed=run.aws(['iam','get-policy','--policy-arn',arn])['Policy']
    version=run.aws(['iam','get-policy-version','--policy-arn',arn,'--version-id',observed['DefaultVersionId']])['PolicyVersion']['Document']
    attachments=run.aws(['iam','list-attached-role-policies','--role-name','seokpan-tf-rosa'])['AttachedPolicies']
    boot=run.aws(['iam','get-role-policy','--role-name','seokpan-tf-bootstrap','--policy-name','seokpan-tf-bootstrap'])['PolicyDocument']
    need(doc(version)==doc(expected) and doc(boot)==boot_after and sum(r['PolicyArn']==arn for r in attachments)==1,'APPLIED_RESTORE_PERMISSION_READBACK_DIFFERS')
    result.update(status='PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED',iam_apply_attempted=True)
    M.save(cache,result);return result

def check(a,out):
    accepted,c,manifest,scope,expected=inputs(a);cache=M.read(Path(a.reconstruction_receipt).parent/'restore-policy-accepted.json')
    need(cache.get('status')=='PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED'
         and cache['policy_sha256']==hashlib.sha256(M.canonical(expected)).hexdigest(),'EXACT_RESTORE_POLICY_ACCEPTANCE_REQUIRED')
    run=M.Run(out);caller=run.aws(['sts','get-caller-identity'])
    need(caller.get('Account')==manifest['account_id'] and ':assumed-role/seokpan-tf-rosa/' in caller.get('Arn',''),'ROSA_PURPOSE_ROLE_REQUIRED')
    F=module('scoped_restore_probe',HERE/'rosa-monitoring-finalize.py','a807e1c917d5fdc7055e5b960f5f1705c1268f3730f70b85c8a515c41ac0e972')
    volumes=[F.probe(F.volume_dry_run(r,'preflight-only'),out,i) for i,r in enumerate(scope['volumes'])]
    keys=[]
    for key in scope['kms_keys']:
        observed=run.aws(['kms','describe-key','--key-id',key])['KeyMetadata']
        need(observed.get('Arn')==key,'EXACT_KMS_KEY_REQUIRED')
        keys.append({'state':observed.get('KeyState'),'manager':observed.get('KeyManager'),'usage':observed.get('KeyUsage'),'spec':observed.get('KeySpec')})
    allowed=sum(v['status']=='DRY_RUN_ALLOWED' for v in volumes)
    ready=allowed==4 and all(k=={'state':'Enabled','manager':'AWS','usage':'ENCRYPT_DECRYPT','spec':'SYMMETRIC_DEFAULT'} for k in keys)
    c['restoration']['new_volume_permissions_accepted']=ready
    c['restoration']['permission_preflight_receipt']=str(out/'receipt.json')
    tmp=out/'lifecycle.new.json';M.save(tmp,c);os.replace(tmp,accepted['lifecycle_config'])
    return {'status':'PASS_RESTORE_PERMISSION_PREREQUISITES_NOT_RUNTIME' if ready else 'PENDING_RESTORE_PERMISSION_OR_CUSTOMER_KEY_POLICY',
        'volume_dry_run_allowed':allowed,'volume_results':volumes,'kms_metadata':keys,'new_volume_permissions_accepted':ready,
        'kms_cryptographic_runtime':'NOT_TESTED','actual_new_cluster_check':'PENDING_NEW_CLUSTER','new_volumes_created':0,
        'deletion_ready':False,'deletion_started':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('apply','check'))
    for n in ('reconstruction-receipt','scope-inputs','snapshot-permission-run','restore-hcl','output-parent'):p.add_argument('--'+n,required=True)
    p.add_argument('--apply-exact',action='store_true');a=p.parse_args();os.umask(0o077)
    out=Path(tempfile.mkdtemp(prefix='monitoring-restore-permission-',dir=a.output_parent))
    try:result=apply_policy(a,out) if a.mode=='apply' else check(a,out)
    except Exception as error:result={'status':'STOP_RESTORE_PERMISSION','reason':str(error) if type(error) is RuntimeError else type(error).__name__,'automatic_retry':False}
    result.update(deletion_ready=False,deletion_started=False)
    M.save(out/'receipt.json',result);print(json.dumps(result,indent=2));print('Private restoration permission directory: '+str(out))
    return 0 if result['status'].startswith(('PASS_','PENDING_')) else 1

if __name__=='__main__':raise SystemExit(main())
