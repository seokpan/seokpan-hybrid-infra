"""New exact Bootstrap increment boundaries; synthetic files/commands only."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('permission',Path(__file__).with_name('rosa-monitoring-permission.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def policy():
    return {'Version':'2012-10-17','Statement':[{'Sid':'FixtureOnly','Effect':'Allow','Action':'ec2:DescribeVolumes',
        'Resource':'*','Condition':{'StringEquals':{'aws:RequestedRegion':'ap-northeast-2'}}}]}
def plan():
    return {'complete':True,'applyable':True,'errored':False,'resource_changes':[{
        'address':M.TARGET,'mode':'managed','type':'aws_iam_role_policy',
        'change':{'actions':['create'],'before':None,'after':{'name':M.POLICY,'role':M.ROLE,'policy':json.dumps(policy())}}}],
        'output_changes':{}}

class ExactMonitoringPermission(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).parent.parent/'terraform/bootstrap').is_dir(),'Repository-only pinned source check')
    def test_pinned_bootstrap_sources_match_checkout(self):
        self.assertTrue(M.source_files(Path(__file__).parent.parent).is_dir())
    def test_only_exact_inline_policy_create_passes(self):
        self.assertEqual(M.review(plan(),policy()),{'inline_policy_create':1,'managed_update':0,'managed_delete':0,'other_managed_changes':0})
    def test_other_role_trust_or_network_changes_stop(self):
        for kind in ('aws_iam_role','aws_nat_gateway','aws_eip'):
            p=plan();p['resource_changes'].append({'address':kind+'.foreign','mode':'managed','type':kind,
                'change':{'actions':['delete'],'before':{},'after':None}})
            with self.assertRaisesRegex(RuntimeError,'UNRELATED_BOOTSTRAP'):M.review(p,policy())
    def test_existing_policy_update_or_replacement_is_not_accepted(self):
        for actions in (['update'],['delete','create'],['no-op']):
            p=plan();p['resource_changes'][0]['change']['actions']=actions
            with self.assertRaises(RuntimeError):M.review(p,policy())
    def test_wildcard_or_removed_region_cannot_match_expected_policy(self):
        p=plan();value=policy();value['Statement'][0]['Action']='ec2:*'
        p['resource_changes'][0]['change']['after']['policy']=json.dumps(value)
        with self.assertRaisesRegex(RuntimeError,'SCOPE_DIFFERS'):M.review(p,policy())
    def test_semantic_singleton_order_normalization_does_not_broaden_policy(self):
        value=policy();s=value['Statement'][0];s['Action']=[s['Action']];s['Resource']=[s['Resource']]
        s['Condition']['StringEquals']['aws:RequestedRegion']=['ap-northeast-2']
        self.assertEqual(M.policy_value(policy()),M.policy_value(value))
    def test_drift_output_changes_and_incomplete_plan_block_apply(self):
        for key,value in [('resource_drift',[{'fixture':'drift'}]),('output_changes',{'other':{'actions':['update']}}),('complete',False)]:
            p=plan();p[key]=value
            with self.assertRaises(RuntimeError):M.review(p,policy())
    def test_unreviewed_policy_grammar_cannot_hide_notaction_or_principal(self):
        value=policy();value['Statement'][0]['NotAction']='ec2:DescribeVolumes'
        with self.assertRaisesRegex(RuntimeError,'GRAMMAR'):M.policy_value(value)
    def test_unclassified_or_wrong_denial_stops_before_any_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'r.json';M.save(p,{'status':'STOP_RECONSTRUCTION_PREPARATION'})
            with patch.object(M.subprocess,'run') as calls:
                with self.assertRaisesRegex(RuntimeError,'ACCEPTED_RECONSTRUCTION'):M.preflight(p)
                calls.assert_not_called()
    def test_existing_vpc_and_data_permissions_remain_enabled_with_same_scope(self):
        account='123456789012'
        rows=[]
        for name,resources in [('tf_rosa_first_plan_read',['arn:aws:ec2:ap-northeast-2:'+account+':vpc/vpc-abc']),
              ('tf_rosa_data_connection',['arn:aws:ec2:ap-northeast-2:'+account+':security-group/sg-abc',
                                          'arn:aws:ec2:ap-northeast-2:'+account+':security-group/sg-def'])]:
            rows.append({'mode':'managed','type':'aws_iam_role_policy','name':name,
                         'instances':[{'attributes':{'policy':json.dumps({'Statement':[{'Resource':resources}]})}}]})
        inputs={'foundation':{'vpc_id':'vpc-abc','data_security_groups':{'mariadb':'sg-abc','redis':'sg-def'}}}
        flags=M.previous_settings({'resources':rows},inputs,account)
        self.assertTrue(flags['enable_rosa_plan_read_permissions']);self.assertTrue(flags['enable_rosa_data_connection_permissions'])
        self.assertEqual(flags['rosa_data_security_group_ids'],{'rds':'sg-abc','redis':'sg-def'})
        inputs['foundation']['data_security_groups']['redis']='sg-999'
        with self.assertRaisesRegex(RuntimeError,'EXISTING_DATA_PERMISSION_SCOPE_DIFFERS'):M.previous_settings({'resources':rows},inputs,account)
    def test_absent_existing_permissions_are_not_enabled_by_guess(self):
        flags=M.previous_settings({'resources':[]},{},'123456789012')
        self.assertTrue(flags and all(v is False for v in flags.values()))
    def test_partial_existing_account_attachment_requires_reconciliation(self):
        state={'resources':[{'mode':'managed','type':'aws_iam_policy','name':'tf_foundation_rosa_iam','instances':[{'attributes':{}}]}]}
        with self.assertRaisesRegex(RuntimeError,'PARTIAL_EXISTING_ACCOUNT_PERMISSION'):M.previous_settings(state,{},'123456789012')

    def execute_fixture(self,root,apply,race=False):
        source=root/'source-original';source.mkdir();(source/'main.tf').write_text('fixture-only')
        out=root/'out';out.mkdir();scope=root/'scope.json';M.save(scope,{'fixture':'scope'})
        receipt=root/'reconstruction/receipt.json';receipt.parent.mkdir();M.save(receipt,{})
        accepted={'terraform_scope_inputs':str(scope),'policy_sha256':'f'*64}
        inputs=root/'inputs.json';M.save(inputs,{'foundation':{}})
        state={'lineage':'fixture-lineage','serial':1}
        class FakeRun:
            instance=None
            def __init__(self,folder):self.env={};self.out=folder;self.applies=0;self.pulls=0;self.policy_reads=0;FakeRun.instance=self
            def aws(self,args):
                if args[:2]==['iam','list-role-policies']:return {'PolicyNames':[]}
                return {'Account':'123456789012','Arn':'arn:aws:sts::123456789012:assumed-role/seokpan-tf-bootstrap/test'}
            def role_policy(self):
                self.policy_reads+=1
                return policy() if self.policy_reads>=3 else None
            def command(self,args,codes=(0,)):
                if 'version' in args:data={'terraform_version':'1.16.4'}
                elif 'init' in args:
                    d=Path(self.env['TF_DATA_DIR']);d.mkdir();M.save(d/'terraform.tfstate',{'backend':{'type':'s3','config':{
                        'key':'phase2/bootstrap/terraform.tfstate','bucket':'seokpan-tfstate-123456789012','region':'ap-northeast-2'}}});data={}
                elif 'state' in args:
                    self.pulls+=1;data=dict(state,serial=2 if race and self.pulls>1 else 1)
                elif 'plan' in args:
                    Path(next(x.split('=',1)[1] for x in args if x.startswith('-out='))).write_bytes(b'fixture-plan');data={}
                elif 'show' in args:data=plan()
                elif 'apply' in args:self.applies+=1;data={}
                else:raise AssertionError(args)
                return subprocess.CompletedProcess(args,0,json.dumps(data).encode(),b'')
        a=argparse.Namespace(reconstruction_receipt=str(receipt),apply_exact_policy=apply)
        patches=(patch.object(M,'preflight',return_value=(accepted,{'create':{'var_file':str(inputs)}}, {'account_id':'123456789012'},source,policy())),
                 patch.object(M,'Run',FakeRun),patch.object(M,'SOURCE_SHA256',{'main.tf':M.sha(source/'main.tf')}))
        with patches[0],patches[1],patches[2]:
            if race:
                with self.assertRaisesRegex(RuntimeError,'STATE_CHANGED_NO_APPLY'):M.execute(a,out)
                return FakeRun.instance
            result=M.execute(a,out)
        return result,FakeRun.instance
    def test_default_plan_mode_has_zero_apply_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run=self.execute_fixture(Path(tmp),False)
            self.assertEqual(run.applies,0);self.assertEqual(result['status'],'PASS_EXACT_MONITORING_SNAPSHOT_POLICY_PLAN_NOT_APPLIED')
    def test_exact_apply_is_saved_plan_once_and_readback_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run=self.execute_fixture(Path(tmp),True)
            self.assertEqual(run.applies,1);self.assertEqual(result['status'],'PASS_EXACT_MONITORING_SNAPSHOT_POLICY_APPLIED_AND_READBACK')
    def test_state_race_stops_before_apply(self):
        with tempfile.TemporaryDirectory() as tmp:self.assertEqual(self.execute_fixture(Path(tmp),True,race=True).applies,0)
    def test_existing_inline_policies_count_toward_role_aggregate_limit(self):
        self.assertLess(M.inline_size([policy(),policy()]),10240)
        with self.assertRaisesRegex(RuntimeError,'AGGREGATE_LIMIT_EXCEEDED'):M.inline_size([{'fixture':'x'*10240},policy()])

if __name__=='__main__':unittest.main()
