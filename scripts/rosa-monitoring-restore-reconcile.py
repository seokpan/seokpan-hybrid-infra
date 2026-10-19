"""Reconcile a marked partial restore-IAM Apply and apply only its two missing creates."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile

HERE=Path(__file__).resolve().parent
raw=(HERE/'rosa-monitoring-restore-permission.py').read_bytes()
if hashlib.sha256(raw).hexdigest()!='26e18086c3c5e654fe76e7d71a29659dcec274f0509775e1b4befbc59410b9ba':
    raise RuntimeError('PINNED_RESTORE_HELPER_REQUIRED')
spec=importlib.util.spec_from_file_location('partial_restore_base',HERE/'rosa-monitoring-restore-permission.py')
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
M=R.M;need=M.need

class TracedRun(M.Run):
    def __init__(self,out):
        super().__init__(out);self.phase='LOCAL_RECONCILIATION';self.last_operation='NONE';self.accepted={}
    def command(self,args,codes=(0,)):
        if args[0]=='aws':
            self.last_operation='aws '+args[5]+' '+args[6]
        elif args[0]=='terraform':
            self.last_operation='terraform '+args[2]
        else:self.last_operation='OTHER_COMMAND'
        return super().command(args,codes=codes)

def review_remaining(plan,expected,account,snapshot,boot_before,boot_after):
    """The earlier Bootstrap grant must stay a no-op; only policy and attachment may be created."""
    need(plan.get('complete') is True and plan.get('applyable') is True and not plan.get('errored'),
         'COMPLETE_REMAINDER_PLAN_REQUIRED')
    checked=copy.deepcopy(plan);rows={r['address']:r for r in plan.get('resource_changes',[]) if r.get('mode')=='managed'}
    kept=[];metadata=0
    for drift in checked.get('resource_drift',[]):
        b,a=drift['change'].get('before'),drift['change'].get('after')
        if drift['address']=='aws_iam_role.tf["bootstrap"]' and isinstance(b,dict) and isinstance(a,dict) and b.get('inline_policy')!=a.get('inline_policy'):
            changed={k for k in set(b)|set(a) if b.get(k)!=a.get(k) or (k in b)!=(k in a)}
            need(drift.get('mode')=='managed' and drift.get('type')=='aws_iam_role' and not drift.get('module_address')
                 and drift['change']['actions']==['update'] and a.get('name')==b.get('name')=='seokpan-tf-bootstrap'
                 and changed=={'inline_policy'},'ONLY_RECORDED_BOOTSTRAP_INLINE_METADATA_REFRESH')
            def named(values):
                need(isinstance(values,list) and len({v['name'] for v in values})==len(values),'UNIQUE_INLINE_METADATA_REQUIRED')
                return {v['name']:R.doc(v['policy']) for v in values}
            prior,current=named(b['inline_policy']),named(a['inline_policy']);name='seokpan-tf-bootstrap'
            need(prior.get(name)==R.doc(boot_before) and current.get(name)==R.doc(boot_after)
                 and {k:v for k,v in prior.items() if k!=name}=={k:v for k,v in current.items() if k!=name},
                 'ONLY_ACCEPTED_BOOTSTRAP_DOCUMENT_METADATA_CHANGE')
            c=rows.get(drift['address'],{}).get('change',{})
            need(c.get('actions')==['no-op'] and c.get('before')==a and c.get('after')==a,'BOOTSTRAP_ROLE_METADATA_MUST_BE_NOOP')
            metadata+=1
        else:kept.append(drift)
    checked['resource_drift']=kept;R.refresh_review(checked,snapshot)
    arn='arn:aws:iam::'+account+':policy/'+R.NAME;changed=set();boot_seen=False
    for row in plan.get('resource_changes',[]):
        if row.get('mode')=='data':continue
        need(not row.get('module_address') and not row.get('previous_address') and not row['change'].get('importing'),
             'NO_MODULE_MOVE_OR_IMPORT')
        c=row['change'];a=c.get('after') or {};address=row['address']
        if c['actions']==['no-op']:
            need(c.get('before')==c.get('after'),'CHANGED_NOOP_REJECTED')
            if address==R.BOOT:
                need(a.get('name')==a.get('role')=='seokpan-tf-bootstrap' and R.doc(a['policy'])==R.doc(boot_after),
                     'PARTIAL_BOOTSTRAP_GRANT_MUST_REMAIN_EXACT')
                boot_seen=True
            continue
        need(address not in changed,'DUPLICATE_REMAINDER_CHANGE')
        if address==R.TARGET:
            need(c['actions']==['create'] and a.get('name')==R.NAME and a.get('path')=='/'
                 and R.doc(a['policy'])==R.doc(expected),'EXACT_RESTORE_POLICY_CREATE_ONLY')
        elif address==R.ATTACH:
            need(c['actions']==['create'] and a.get('role')=='seokpan-tf-rosa' and a.get('policy_arn')==arn,
                 'EXACT_RESTORE_ATTACHMENT_CREATE_ONLY')
        else:raise RuntimeError('UNRELATED_REMAINDER_CHANGE_REJECTED')
        changed.add(address)
    need(boot_seen and changed=={R.TARGET,R.ATTACH}
         and all(v['actions']==['no-op'] for v in plan.get('output_changes',{}).values()),
         'EXACT_TWO_CREATES_WITH_BOOTSTRAP_NOOP_REQUIRED')
    return metadata

def bootstrap_state(state,expected):
    rows=[r for r in state.get('resources',[]) if r.get('mode')=='managed' and not r.get('module')
          and r.get('type')=='aws_iam_role_policy' and r.get('name')=='tf_bootstrap']
    need(len(rows)==1 and len(rows[0].get('instances',[]))==1,'CURRENT_BOOTSTRAP_STATE_RESOURCE_REQUIRED')
    a=rows[0]['instances'][0]['attributes']
    need(a.get('name')==a.get('role')=='seokpan-tf-bootstrap' and R.doc(a['policy'])==R.doc(expected),
         'PARTIAL_BOOTSTRAP_STATE_REQUIRES_OWNER_REVIEW')

def reconcile(a,out,run):
    accepted,c,manifest,scope,expected=R.inputs(a);account=manifest['account_id']
    old=Path(a.resume_from);marker=Path(a.reconstruction_receipt).parent/'restore-policy-apply.NO_AUTOMATIC_RETRY.json'
    cache=marker.with_name('restore-policy-accepted.json');next_marker=marker.with_name('restore-policy-remainder-apply.NO_AUTOMATIC_RETRY.json')
    receipt=M.read(old/'receipt.json');binding=M.read(marker);marker_sha=M.sha(marker)
    need(receipt.get('reason')=='COMMAND_FAILED_PRIVATE_LOG_NO_RETRY' and binding.get('result_directory')==str(old),
         'EXACT_PARTIAL_RUN_MARKER_REQUIRED')
    need(not cache.exists() and not cache.is_symlink() and not next_marker.exists() and not next_marker.is_symlink(),
         'PRIOR_RECONCILIATION_REQUIRES_READ_ONLY_REVIEW')
    original=M.source_files(Path(a.snapshot_permission_run)/'source');target=old/'source/terraform/bootstrap'
    need({p.name for p in target.iterdir() if p.is_file()}==set(M.SOURCE_SHA256)|{'rosa_monitoring_restore.tf'},
         'EXACT_PARTIAL_SOURCE_SET_REQUIRED')
    for name in M.SOURCE_SHA256:
        expected_raw=(original/name).read_bytes()
        if name=='iam.tf':expected_raw=expected_raw.decode().replace(R.OLD_SOURCE,R.NEW_SOURCE).encode()
        need((target/name).read_bytes()==expected_raw,'PARTIAL_SOURCE_CHANGED')
    need(M.sha(target/'rosa_monitoring_restore.tf')==R.NEW_HCL_SHA,'PARTIAL_RESTORE_HCL_CHANGED')
    pins={p.name:M.sha(p) for p in target.iterdir() if p.is_file()}
    saved_old=old/'saved.tfplan';review=M.read(old/'plan-review.json');old_plan=M.read(old/'plan-private.json')
    need(M.sha(saved_old)==review.get('plan_sha256')==binding.get('plan_sha256'),'PARTIAL_SAVED_PLAN_BINDING_REQUIRED')
    boot_after=R.review(old_plan,expected,account,M.read(accepted['policy_file']))
    boot_before=next(r['change']['before']['policy'] for r in old_plan['resource_changes'] if r['address']==R.BOOT)
    run.env['TF_DATA_DIR']=str(old/'tf-data');tf=['terraform','-chdir='+str(target)]
    backend=M.read(old/'tf-data/terraform.tfstate')['backend']
    need(backend['type']=='s3' and backend['config']['key']=='phase2/bootstrap/terraform.tfstate'
         and backend['config']['bucket']=='seokpan-tfstate-'+account and backend['config']['region']=='ap-northeast-2',
         'EXACT_EXISTING_BOOTSTRAP_BACKEND_REQUIRED')
    run.phase='PARTIAL_APPLY_READ_ONLY_RECONCILIATION'
    caller=run.aws(['sts','get-caller-identity'])
    need(caller.get('Account')==account and ':assumed-role/seokpan-tf-bootstrap/' in caller.get('Arn',''),'BOOTSTRAP_PURPOSE_ROLE_REQUIRED')
    need(json.loads(run.command(tf+['version','-json']).stdout)['terraform_version']=='1.16.4','ADOPTED_TERRAFORM_REQUIRED')
    need(json.loads(run.command(tf+['show','-json',str(saved_old)]).stdout)==old_plan,'PARTIAL_BINARY_PLAN_DIFFERS')
    before=json.loads(run.command(tf+['state','pull']).stdout);prior=M.read(old/'before.tfstate')
    need(before['lineage']==prior['lineage'] and before['serial']>=prior['serial'],'PARTIAL_STATE_LINEAGE_CHANGED')
    bootstrap_state(before,boot_after);R.require_create_only_state(before,'arn:aws:iam::'+account+':policy/'+R.NAME)
    live=run.aws(['iam','get-role-policy','--role-name','seokpan-tf-bootstrap','--policy-name','seokpan-tf-bootstrap'])['PolicyDocument']
    need(R.doc(live)==R.doc(boot_after),'LIVE_BOOTSTRAP_GRANT_DIFFERS_NO_WRITE')
    run.accepted['bootstrap_grant_state_and_live_readback']=True
    arn='arn:aws:iam::'+account+':policy/'+R.NAME
    need(run.aws(['iam','get-policy','--policy-arn',arn],missing=True) is None,'POLICY_EXISTS_REQUIRES_RECONCILIATION_NO_TAKEOVER')
    R.require_unattached(run.aws(['iam','list-attached-role-policies','--role-name','seokpan-tf-rosa'])['AttachedPolicies'],arn)
    values=M.read(old/'restore-scope.tfvars.json')
    need(all(values.get(k)==v for k,v in M.previous_settings(before,M.read(c['create']['var_file']),account).items()),
         'CURRENT_FLAGS_CHANGED_NO_AUTOMATIC_REPLAN')
    need(values.get('enable_rosa_monitoring_restore') is True
         and R.doc(values['rosa_monitoring_restore_policy_json'])==R.doc(expected),'PARTIAL_SCOPE_CHANGED')
    R.bind_terraform_sources(values,scope)
    varfile=out/'remainder.tfvars.json';M.save(varfile,values);varsha=M.sha(varfile);M.save(out/'before.tfstate',before)
    saved=out/'remainder.tfplan';run.phase='PLAN_MISSING_POLICY_AND_ATTACHMENT_ONLY'
    run.command(tf+['plan','-input=false','-no-color','-lock-timeout=60s','-detailed-exitcode','-var-file='+str(varfile),'-out='+str(saved)],codes=(0,2))
    plan=json.loads(run.command(tf+['show','-json',str(saved)]).stdout);M.save(out/'plan-private.json',plan)
    metadata=review_remaining(plan,expected,account,M.read(accepted['policy_file']),boot_before,boot_after)
    result={'status':'PASS_PARTIAL_RECONCILED_TWO_CREATE_PLAN_NOT_APPLIED','managed_policy_create':1,'role_attachment_create':1,
        'bootstrap_inline_update':0,'bootstrap_grant_reused':True,'bootstrap_metadata_refresh':metadata,
        'policy_sha256':hashlib.sha256(M.canonical(expected)).hexdigest(),'plan_sha256':M.sha(saved),
        'old_apply_marker_preserved':True,'old_plan_applied_again':False,'new_volumes_created':0,'deletion_ready':False}
    M.save(out/'plan-review.json',result)
    if not a.apply_remainder:return result
    run.phase='REMAINDER_PREAPPLY_BINDING'
    current=json.loads(run.command(tf+['state','pull']).stdout)
    need((current['lineage'],current['serial'])==(before['lineage'],before['serial']) and M.sha(marker)==marker_sha
         and M.sha(varfile)==varsha and M.sha(saved)==result['plan_sha256']
         and all(M.sha(target/n)==v for n,v in pins.items()),'STATE_SOURCE_OR_MARKER_CHANGED_NO_APPLY')
    need(R.doc(run.aws(['iam','get-role-policy','--role-name','seokpan-tf-bootstrap','--policy-name','seokpan-tf-bootstrap'])['PolicyDocument'])==R.doc(boot_after),
         'BOOTSTRAP_GRANT_CHANGED_NO_APPLY')
    need(run.aws(['iam','get-policy','--policy-arn',arn],missing=True) is None,'POLICY_CREATED_CONCURRENTLY_NO_TAKEOVER')
    R.require_unattached(run.aws(['iam','list-attached-role-policies','--role-name','seokpan-tf-rosa'])['AttachedPolicies'],arn)
    with next_marker.open('x') as stream:json.dump({'plan_sha256':result['plan_sha256'],'result_directory':str(out),
        'reconciled_run':str(old),'old_marker_sha256':marker_sha},stream)
    run.phase='APPLY_TWO_MISSING_CREATES';run.accepted['remainder_apply_attempted']=True
    run.command(tf+['apply','-input=false','-no-color','-lock-timeout=60s',str(saved)])
    run.phase='EXACT_REMAINDER_READBACK'
    observed=run.aws(['iam','get-policy','--policy-arn',arn])['Policy']
    version=run.aws(['iam','get-policy-version','--policy-arn',arn,'--version-id',observed['DefaultVersionId']])['PolicyVersion']['Document']
    attachments=run.aws(['iam','list-attached-role-policies','--role-name','seokpan-tf-rosa'])['AttachedPolicies']
    live=run.aws(['iam','get-role-policy','--role-name','seokpan-tf-bootstrap','--policy-name','seokpan-tf-bootstrap'])['PolicyDocument']
    need(R.doc(version)==R.doc(expected) and R.doc(live)==R.doc(boot_after)
         and sum(row['PolicyArn']==arn for row in attachments)==1,'REMAINDER_READBACK_DIFFERS_NO_RETRY')
    result.update(status='PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED',iam_apply_attempted=True,
                  partial_apply_reconciled=True)
    M.save(cache,result);return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('resume-from','reconstruction-receipt','scope-inputs','snapshot-permission-run','restore-hcl','output-parent'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--apply-remainder',action='store_true');a=parser.parse_args();os.umask(0o077)
    out=Path(tempfile.mkdtemp(prefix='monitoring-restore-reconcile-',dir=a.output_parent));run=TracedRun(out)
    try:result=reconcile(a,out,run)
    except Exception as error:
        result={'status':'STOP_RESTORE_RECONCILIATION','reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'phase':run.phase,'failed_operation':run.last_operation,**run.accepted,'automatic_retry':False}
    result.update(deletion_ready=False,deletion_started=False)
    M.save(out/'receipt.json',result);print(json.dumps(result,indent=2));print('Private reconciliation directory: '+str(out))
    return 0 if result['status'].startswith('PASS_') else 1

if __name__=='__main__':raise SystemExit(main())
