"""New metadata-only drift and saved-plan resume boundaries; no real APIs."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('saved_permission',Path(__file__).with_name('rosa-monitoring-permission.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def policy():
    return {'Version':'2012-10-17','Statement':[{'Sid':'Fixture','Effect':'Allow','Action':'ec2:DescribeVolumes',
        'Resource':'*','Condition':{'StringEquals':{'aws:RequestedRegion':'ap-northeast-2'}}}]}

def plan():
    p={'complete':True,'applyable':True,'errored':False,'output_changes':{},'resource_changes':[{
        'address':M.TARGET,'mode':'managed','type':'aws_iam_role_policy',
        'change':{'actions':['create'],'before':None,'after':{'name':M.POLICY,'role':M.ROLE,'policy':json.dumps(policy())}}}],
        'resource_drift':[]}
    for address in ['aws_iam_policy.tf_foundation_hybrid','aws_iam_policy.tf_foundation_hybrid_ec2','aws_iam_role.tf["foundation"]']:
        role=address.startswith('aws_iam_role.')
        before={'name':'seokpan-tf-foundation','managed_policy_arns':[]} if role else {'policy':'unchanged','attachment_count':0}
        after=dict(before)
        if role:after['managed_policy_arns']=['arn:aws:iam::123456789012:policy/fixture']
        else:after['attachment_count']=1
        row={'address':address,'mode':'managed','type':'aws_iam_role' if role else 'aws_iam_policy',
             'change':{'actions':['update'],'before':before,'after':after}}
        p['resource_drift'].append(row)
        noop=copy.deepcopy(row);noop['change']={'actions':['no-op'],'before':after,'after':after}
        p['resource_changes'].append(noop)
    return p

class SavedResume(unittest.TestCase):
    def test_default_rejects_even_reviewed_metadata(self):
        with self.assertRaisesRegex(RuntimeError,'DRIFT_REQUIRES'):M.review(plan(),policy())
    def test_only_reviewed_three_noop_fields_pass(self):
        self.assertEqual(M.review(plan(),policy(),True)['inline_policy_create'],1)
        self.assertEqual(M.reviewed_refresh(plan())['attachment_writes'],0)
    def test_policy_or_trust_drift_stops(self):
        for index,key in [(0,'policy'),(2,'assume_role_policy')]:
            p=plan();p['resource_drift'][index]['change']['after'][key]='changed'
            with self.assertRaises(RuntimeError):M.review(p,policy(),True)
    def test_attachment_write_or_refreshed_noop_mismatch_stops(self):
        for actions in (['update'],['delete','create']):
            p=plan();p['resource_changes'][3]['change']['actions']=actions
            with self.assertRaisesRegex(RuntimeError,'IDENTICAL_NOOP'):M.review(p,policy(),True)
        p=plan();p['resource_changes'][1]['change']['before']={'attachment_count':99}
        with self.assertRaisesRegex(RuntimeError,'IDENTICAL_NOOP'):M.review(p,policy(),True)
    def test_missing_extra_or_foreign_drift_stops(self):
        for mutate in ('missing','extra','foreign'):
            p=plan()
            if mutate=='missing':p['resource_drift'].pop()
            elif mutate=='extra':p['resource_drift'].append(copy.deepcopy(p['resource_drift'][0]))
            else:p['resource_drift'][0]['address']='aws_iam_policy.other'
            with self.assertRaises(RuntimeError):M.review(p,policy(),True)

    def fixture(self,tmp,apply=True,race=False,tamper=False,binary_mismatch=False,policy_mismatch=False):
        root=Path(tmp);old=root/'old';old.mkdir();out=root/'out';out.mkdir();recon=root/'recon';recon.mkdir()
        p=plan();source=old/'source/terraform/bootstrap';source.mkdir(parents=True)
        for name in ('main.tf','.terraform.lock.hcl'):(source/name).write_text('fixture')
        hashes={name:M.sha(source/name) for name in ('main.tf','.terraform.lock.hcl')}
        M.save(old/'receipt.json',{'reason':'BOOTSTRAP_DRIFT_REQUIRES_OWNER_REVIEW_NO_APPLY'})
        M.save(old/'before.tfstate',{'serial':17,'lineage':'fixture'})
        M.save(old/'plan-private.json',p);M.save(old/'monitoring-scope.tfvars.json',{})
        (old/'saved.tfplan').write_bytes(b'fixture-plan');(old/'saved.tfplan').chmod(0o600)
        (old/'tf-data').mkdir();M.save(old/'tf-data/terraform.tfstate',{'backend':{'type':'s3','config':{
            'key':'phase2/bootstrap/terraform.tfstate','bucket':'seokpan-tfstate-123456789012','region':'ap-northeast-2'}}})
        pins={name:M.sha(old/name) for name in M.REVIEWED_SAVED_INPUTS}
        if tamper:(old/'saved.tfplan').write_bytes(b'changed')
        class FakeRun:
            def __init__(self):self.env={};self.calls=[];self.reads=0
            def role_policy(self):
                self.reads+=1
                if self.reads==1:return None
                value=policy()
                if policy_mismatch:value['Statement'][0]['Action']='ec2:DescribeSnapshots'
                return value
            def command(self,args):
                self.calls.append(args)
                if 'version' in args:value={'terraform_version':'1.16.4'}
                elif 'show' in args:value={} if binary_mismatch else p
                elif 'state' in args:value={'serial':18 if race else 17,'lineage':'fixture'}
                elif 'apply' in args:value={}
                else:raise AssertionError('Unexpected command')
                return subprocess.CompletedProcess(args,0,json.dumps(value).encode(),b'')
        run=FakeRun();a=argparse.Namespace(resume_permission_from=str(old),apply_exact_policy=apply,reconstruction_receipt=str(recon/'receipt.json'))
        with patch.object(M,'SOURCE_SHA256',hashes),patch.object(M,'REVIEWED_SAVED_INPUTS',pins):
            try:return M.resume_saved(a,out,{'policy_sha256':'f'*64},{'account_id':'123456789012'},policy(),run,500),run,recon
            except RuntimeError as e:return str(e),run,recon
    def test_saved_apply_once_without_plan_or_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,run,recon=self.fixture(tmp)
            self.assertEqual(r['status'],'PASS_EXACT_MONITORING_SNAPSHOT_POLICY_APPLIED_AND_READBACK')
            self.assertEqual(sum('apply' in x for x in run.calls),1)
            self.assertFalse(any('plan' in x or 'init' in x for x in run.calls))
            self.assertTrue((recon/'snapshot-permission-apply.NO_AUTOMATIC_RETRY.json').exists())
    def test_state_race_and_plan_hash_stop_before_apply(self):
        for kwargs in ({'race':True},{'tamper':True},{'binary_mismatch':True}):
            with tempfile.TemporaryDirectory() as tmp:
                result,run,_=self.fixture(tmp,**kwargs)
                self.assertIsInstance(result,str);self.assertFalse(any('apply' in x for x in run.calls))
    def test_plan_only_preserves_zero_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,run,_=self.fixture(tmp,apply=False)
            self.assertFalse(r['iam_apply_attempted']);self.assertFalse(any('apply' in x for x in run.calls))
    def test_readback_failure_preserves_apply_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,run,recon=self.fixture(tmp,policy_mismatch=True)
            self.assertEqual(r,'APPLIED_POLICY_READBACK_DIFFERS_NO_REAPPLY')
            self.assertEqual(sum('apply' in x for x in run.calls),1)
            self.assertTrue((recon/'snapshot-permission-apply.NO_AUTOMATIC_RETRY.json').exists())

if __name__=='__main__':unittest.main()
