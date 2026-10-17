"""Synthetic partial-Apply reconciliation; no AWS credentials, APIs or old Apply replay."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('reconcile',Path(__file__).with_name('rosa-monitoring-restore-reconcile.py'))
N=importlib.util.module_from_spec(spec);spec.loader.exec_module(N)
import test_rosa_monitoring_restore_permission as T
R=N.R;M=N.M;ACCOUNT=T.ACCOUNT

def plans():
    old,expected,boot_before=T.plan();new=copy.deepcopy(old)
    row=new['resource_changes'][0];boot_after=row['change']['after']['policy']
    row['change']={'actions':['no-op'],'before':row['change']['after'],'after':row['change']['after']}
    return old,new,expected,boot_before,json.loads(boot_after)

class PartialRestore(unittest.TestCase):
    def test_remainder_has_only_two_creates_and_no_bootstrap_write(self):
        _,new,expected,before,after=plans()
        self.assertEqual(N.review_remaining(new,expected,ACCOUNT,{},before,after),0)
    def test_bootstrap_rewrite_import_and_foreign_delete_are_rejected(self):
        for kind in ['boot-write','import','foreign-delete']:
            old,new,expected,before,after=plans()
            if kind=='boot-write':new['resource_changes'][0]=old['resource_changes'][0]
            elif kind=='import':new['resource_changes'][1]['change']['importing']={'id':'foreign'}
            else:new['resource_changes'].append({'address':'aws_nat_gateway.foreign','mode':'managed','change':{'actions':['delete'],'before':{},'after':None}})
            with self.assertRaises(RuntimeError):N.review_remaining(new,expected,ACCOUNT,{},before,after)
    def test_only_the_already_accepted_bootstrap_metadata_refresh_passes(self):
        _,new,expected,before,after=plans()
        b={'name':'seokpan-tf-bootstrap','inline_policy':[{'name':'seokpan-tf-bootstrap','policy':json.dumps(before)}]}
        a=dict(b,inline_policy=[{'name':'seokpan-tf-bootstrap','policy':json.dumps(after)}])
        row={'address':'aws_iam_role.tf["bootstrap"]','type':'aws_iam_role','mode':'managed',
             'change':{'actions':['no-op'],'before':a,'after':a}}
        new['resource_changes'].append(row)
        new['resource_drift']=[dict(row,change={'actions':['update'],'before':b,'after':a})]
        self.assertEqual(N.review_remaining(new,expected,ACCOUNT,{},before,after),1)
        a['inline_policy'][0]['policy']=json.dumps(before)
        with self.assertRaises(RuntimeError):N.review_remaining(new,expected,ACCOUNT,{},before,after)

    def fixture(self,tmp,fail=None):
        root=Path(tmp);recon=root/'recon';recon.mkdir();old=root/'partial';old.mkdir();out=root/'out';out.mkdir()
        original=root/'original';original.mkdir();(original/'iam.tf').write_bytes(R.OLD_SOURCE.encode());(original/'.terraform.lock.hcl').write_text('fixture')
        target=old/'source/terraform/bootstrap';target.mkdir(parents=True)
        (target/'iam.tf').write_bytes(R.NEW_SOURCE.encode());(target/'.terraform.lock.hcl').write_text('fixture')
        hcl=Path(__file__).with_name('rosa_monitoring_restore.tf')
        if not hcl.is_file():hcl=Path(__file__).parent.parent/'terraform/bootstrap/rosa_monitoring_restore.tf'
        (target/'rosa_monitoring_restore.tf').write_bytes(hcl.read_bytes())
        old_plan,new_plan,expected,boot_before,boot_after=plans()
        snapshot=root/'snapshot.json';M.save(snapshot,{'Version':'2012-10-17','Statement':[]})
        adopted=root/'adopted.json';M.save(adopted,{})
        accepted={'policy_file':str(snapshot)};config={'create':{'var_file':str(adopted)}}
        M.save(old/'receipt.json',{'reason':'COMMAND_FAILED_PRIVATE_LOG_NO_RETRY'})
        saved=old/'saved.tfplan';saved.write_bytes(b'original-plan')
        M.save(old/'plan-private.json',old_plan);M.save(old/'plan-review.json',{'plan_sha256':M.sha(saved)})
        marker=recon/'restore-policy-apply.NO_AUTOMATIC_RETRY.json'
        M.save(marker,{'result_directory':str(old),'plan_sha256':M.sha(saved)})
        original_marker=marker.read_bytes()
        M.save(old/'before.tfstate',{'lineage':'fixture','serial':1})
        data=old/'tf-data';data.mkdir();M.save(data/'terraform.tfstate',{'backend':{'type':'s3','config':{
            'key':'phase2/bootstrap/terraform.tfstate','bucket':'seokpan-tfstate-'+ACCOUNT,'region':'ap-northeast-2'}}})
        M.save(old/'restore-scope.tfvars.json',{'enable_rosa_monitoring_restore':True,'rosa_monitoring_restore_policy_json':json.dumps(expected)})
        if fail=='prior-attempt':M.save(recon/'restore-policy-remainder-apply.NO_AUTOMATIC_RETRY.json',{})
        if fail=='plan-binding':saved.write_bytes(b'changed')
        args=argparse.Namespace(resume_from=str(old),reconstruction_receipt=str(recon/'receipt.json'),snapshot_permission_run='/snapshot',apply_remainder=True)
        class FakeRun:
            def __init__(self):self.env={};self.phase='START';self.accepted={};self.last_operation='NONE';self.applies=0;self.plans=0;self.pulls=0;self.calls=[]
            def aws(self,args,missing=False):
                self.calls.append(args[:2])
                if args[:2]==['sts','get-caller-identity']:return {'Account':ACCOUNT,'Arn':'arn:aws:sts::'+ACCOUNT+':assumed-role/seokpan-tf-bootstrap/test'}
                if args[:2]==['iam','get-role-policy']:return {'PolicyDocument':boot_before if fail=='grant-diff' else boot_after}
                if args[:2]==['iam','get-policy']:
                    if fail=='read-denied':raise RuntimeError('IAM_READ_FAILED_NO_FALLBACK')
                    return {'Policy':{'DefaultVersionId':'v1'}} if self.applies or fail=='policy-exists' else None
                if args[:2]==['iam','list-attached-role-policies']:return {'AttachedPolicies':[{'PolicyArn':'arn:aws:iam::'+ACCOUNT+':policy/'+R.NAME}] if self.applies else []}
                if args[:2]==['iam','get-policy-version']:return {'PolicyVersion':{'Document':expected}}
                raise AssertionError(args)
            def command(self,args,codes=(0,)):
                self.calls.append(args)
                if 'version' in args:result={'terraform_version':'1.16.4'}
                elif 'show' in args:result=old_plan if str(saved)==args[-1] else new_plan
                elif 'state' in args:
                    self.pulls+=1;result={'lineage':'fixture','serial':3 if fail=='state-race' and self.pulls>1 else 2,
                        'resources':[{'mode':'managed','type':'aws_iam_role_policy','name':'tf_bootstrap','instances':[{'attributes':{
                            'name':'seokpan-tf-bootstrap','role':'seokpan-tf-bootstrap','policy':json.dumps(boot_after)}}]}]}
                elif 'plan' in args:
                    self.plans+=1;Path(next(x.split('=',1)[1] for x in args if x.startswith('-out='))).write_bytes(b'remainder-only');result={}
                elif 'apply' in args:
                    self.applies+=1
                    if Path(args[-1]).name!='remainder.tfplan':raise AssertionError('Old Apply replayed')
                    if fail=='unknown-apply':raise RuntimeError('COMMAND_OUTCOME_UNKNOWN_NO_RETRY')
                    result={}
                else:raise AssertionError(args)
                return subprocess.CompletedProcess(args,0,json.dumps(result).encode(),b'')
        run=FakeRun();pins={name:M.sha(original/name) for name in ['iam.tf','.terraform.lock.hcl']}
        with patch.object(R,'inputs',return_value=(accepted,config,{'account_id':ACCOUNT},T.scope(),expected)),patch.object(M,'source_files',return_value=original),patch.object(M,'SOURCE_SHA256',pins),patch.object(M,'previous_settings',return_value={}):
            try:result=N.reconcile(args,out,run)
            except RuntimeError as error:result=str(error)
        self.assertEqual(marker.read_bytes(),original_marker)
        return result,run,recon

    def test_partial_grant_is_reused_two_creates_apply_once_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,recon=self.fixture(tmp)
            self.assertEqual(result['status'],'PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED')
            self.assertEqual(run.plans,1);self.assertEqual(run.applies,1);self.assertEqual(result['bootstrap_inline_update'],0)
            self.assertTrue((recon/'restore-policy-accepted.json').exists())
    def test_live_grant_difference_stops_before_any_new_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,_=self.fixture(tmp,'grant-diff')
            self.assertEqual(result,'LIVE_BOOTSTRAP_GRANT_DIFFERS_NO_WRITE');self.assertEqual(run.plans,0);self.assertEqual(run.applies,0)
    def test_fresh_session_read_denial_does_not_trigger_a_retry_or_new_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,_=self.fixture(tmp,'read-denied')
            self.assertEqual(result,'IAM_READ_FAILED_NO_FALLBACK');self.assertEqual(run.plans,0);self.assertEqual(run.applies,0)
    def test_existing_policy_is_not_imported_or_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,_=self.fixture(tmp,'policy-exists')
            self.assertEqual(result,'POLICY_EXISTS_REQUIRES_RECONCILIATION_NO_TAKEOVER');self.assertEqual(run.plans,0)
    def test_state_race_blocks_new_apply_and_preserves_original_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,recon=self.fixture(tmp,'state-race')
            self.assertEqual(result,'STATE_SOURCE_OR_MARKER_CHANGED_NO_APPLY');self.assertEqual(run.applies,0)
            self.assertFalse((recon/'restore-policy-remainder-apply.NO_AUTOMATIC_RETRY.json').exists())
    def test_unknown_remainder_apply_keeps_both_markers_without_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,recon=self.fixture(tmp,'unknown-apply')
            self.assertEqual(result,'COMMAND_OUTCOME_UNKNOWN_NO_RETRY');self.assertEqual(run.applies,1)
            self.assertTrue((recon/'restore-policy-remainder-apply.NO_AUTOMATIC_RETRY.json').exists())
            self.assertFalse((recon/'restore-policy-accepted.json').exists())
    def test_previous_remainder_attempt_blocks_all_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,_=self.fixture(tmp,'prior-attempt')
            self.assertEqual(result,'PRIOR_RECONCILIATION_REQUIRES_READ_ONLY_REVIEW');self.assertFalse(run.calls)
    def test_changed_old_plan_binding_blocks_all_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,_=self.fixture(tmp,'plan-binding')
            self.assertEqual(result,'PARTIAL_SAVED_PLAN_BINDING_REQUIRED');self.assertFalse(run.calls)

if __name__=='__main__':unittest.main()
