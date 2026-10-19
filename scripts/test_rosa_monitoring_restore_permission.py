"""New four-snapshot managed-policy scope and exact three-change Plan boundaries."""
import copy
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('restore_perm',Path(__file__).with_name('rosa-monitoring-restore-permission.py'))
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
ACCOUNT='123456789012'
def scope():
    key='arn:aws:kms:ap-northeast-2:'+ACCOUNT+':key/12345678-1234-1234-1234-123456789012'
    return {'account_id':ACCOUNT,'region':'ap-northeast-2','kms_keys':[key],
        'volumes':[{'snapshot_id':'snap-'+str(i)*17,'snapshot_state':'completed','namespace':'openshift-monitoring','source_kms_key':key} for i in range(4)]}
def plan():
    expected=R.build_policy(scope())
    before={'Version':'2012-10-17','Statement':[{'Sid':'Original','Effect':'Allow','Action':['iam:GetRole'],'Resource':['*']}]}
    boot={'name':'seokpan-tf-bootstrap','role':'seokpan-tf-bootstrap','policy':json.dumps(before)}
    after=dict(boot,policy=json.dumps(R.augment(before,ACCOUNT)))
    rows=[{'address':R.BOOT,'type':'aws_iam_role_policy','mode':'managed','change':{'actions':['update'],'before':boot,'after':after}},
        {'address':R.TARGET,'type':'aws_iam_policy','mode':'managed','change':{'actions':['create'],'before':None,'after':{'name':R.NAME,'path':'/','policy':json.dumps(expected)}}},
        {'address':R.ATTACH,'type':'aws_iam_role_policy_attachment','mode':'managed','change':{'actions':['create'],'before':None,'after':{'role':'seokpan-tf-rosa','policy_arn':'arn:aws:iam::'+ACCOUNT+':policy/'+R.NAME}}}]
    return {'complete':True,'applyable':True,'errored':False,'resource_changes':rows,'output_changes':{}},expected,before

class RestorePermissions(unittest.TestCase):
    def test_policy_requires_four_completed_same_account_sources(self):
        for mutate in ('partial','duplicate','foreign-key','wrong-namespace'):
            s=scope()
            if mutate=='partial':s['volumes'][0]['snapshot_state']='pending'
            elif mutate=='duplicate':s['volumes'][0]['snapshot_id']=s['volumes'][1]['snapshot_id']
            elif mutate=='foreign-key':s['kms_keys']=['*']
            else:s['volumes'][0]['namespace']='other'
            with self.assertRaises(RuntimeError):R.build_policy(s)
    def test_volume_grant_requires_real_parent_snapshot_and_encryption(self):
        p=R.build_policy(scope());s=next(x for x in p['Statement'] if x['Sid']=='CreateOnlyEncryptedSnapshotRestorations')
        self.assertEqual(len(s['Condition']['ArnEquals']['ec2:ParentSnapshot']),4)
        self.assertEqual(s['Condition']['Bool']['ec2:Encrypted'],'true')
        self.assertNotIn('*',[x for r in p['Statement'] for x in r['Resource']])
    def test_kms_exact_key_service_and_grant_conditions(self):
        p=R.build_policy(scope());by={s['Sid']:s for s in p['Statement']}
        self.assertEqual(by['UseExactKeysThroughEc2']['Resource'],scope()['kms_keys'])
        self.assertEqual(by['UseExactKeysThroughEc2']['Condition']['StringEquals']['kms:ViaService'],'ec2.ap-northeast-2.amazonaws.com')
        self.assertEqual(by['ServiceResourceGrantsOnly']['Condition']['Bool']['kms:GrantIsForAWSResource'],'true')
    def test_policy_stays_managed_and_does_not_consume_rosa_inline_quota(self):
        self.assertLess(len(R.M.canonical(R.build_policy(scope()))),6144)
        e=R.extras(ACCOUNT)
        self.assertEqual(e[0]['Resource'],['arn:aws:iam::'+ACCOUNT+':policy/'+R.NAME])
        self.assertEqual(e[1]['Resource'],['arn:aws:iam::'+ACCOUNT+':role/seokpan-tf-rosa'])
    def test_exact_three_changes_pass(self):
        p,e,b=plan();self.assertEqual(R.review(p,e,ACCOUNT,{}),R.augment(b,ACCOUNT))
    def test_old_bootstrap_permission_loss_or_extra_grant_stops(self):
        for extra in (False,True):
            p,e,_=plan();d=json.loads(p['resource_changes'][0]['change']['after']['policy'])
            if extra:d['Statement'].append({'Sid':'Foreign','Effect':'Allow','Action':['iam:*'],'Resource':['*']})
            else:d['Statement']=[s for s in d['Statement'] if s.get('Sid')!='Original']
            p['resource_changes'][0]['change']['after']['policy']=json.dumps(d)
            with self.assertRaisesRegex(RuntimeError,'EXACT_BOOTSTRAP'):R.review(p,e,ACCOUNT,{})
    def test_other_attachment_or_policy_scope_stops(self):
        for index,key,value in [(2,'role','other'),(2,'policy_arn','arn:aws:iam::'+ACCOUNT+':policy/other'),(1,'policy','{}')]:
            p,e,_=plan();p['resource_changes'][index]['change']['after'][key]=value
            with self.assertRaises((RuntimeError,KeyError)):R.review(p,e,ACCOUNT,{})
    def test_foreign_resource_deletion_and_output_change_stop(self):
        p,e,_=plan();p['resource_changes'].append({'address':'aws_nat_gateway.other','mode':'managed','change':{'actions':['delete'],'before':{},'after':None}})
        with self.assertRaises(RuntimeError):R.review(p,e,ACCOUNT,{})
        p,e,_=plan();p['output_changes']={'other':{'actions':['update']}}
        with self.assertRaises(RuntimeError):R.review(p,e,ACCOUNT,{})
    def test_trust_drift_is_rejected(self):
        p,e,_=plan();b={'name':'seokpan-tf-rosa','assume_role_policy':'before'};a=dict(b,assume_role_policy='after')
        p['resource_drift']=[{'address':'aws_iam_role.tf["rosa"]','change':{'actions':['update'],'before':b,'after':a}}]
        with self.assertRaises(RuntimeError):R.review(p,e,ACCOUNT,{})
    def test_accepted_snapshot_inline_metadata_only_noop(self):
        p,e,_=plan();snapshot={'Version':'2012-10-17','Statement':[]}
        before={'name':'seokpan-tf-rosa','inline_policy':[]}
        after=dict(before,inline_policy=[{'name':R.M.POLICY,'policy':json.dumps(snapshot)}])
        row={'address':'aws_iam_role.tf["rosa"]','mode':'managed','change':{'actions':['no-op'],'before':after,'after':after}}
        p['resource_changes'].append(row);p['resource_drift']=[dict(row,change={'actions':['update'],'before':before,'after':after})]
        R.review(p,e,ACCOUNT,snapshot)
        p['resource_drift'][0]['change']['after']['inline_policy'][0]['name']='unmanaged'
        with self.assertRaises(RuntimeError):R.review(p,e,ACCOUNT,snapshot)

    def apply_fixture(self,tmp,race=False,unknown=False,collision=False,state_existing=False,already_attached=False):
        root=Path(tmp);old=root/'old';old.mkdir();out=root/'out';out.mkdir();recon=root/'recon';recon.mkdir()
        source=old/'source/terraform/bootstrap';source.mkdir(parents=True)
        (source/'iam.tf').write_text(R.OLD_SOURCE);(source/'.terraform.lock.hcl').write_text('fixture')
        scopefile=old/'monitoring-scope.tfvars.json';R.M.save(scopefile,{'enable_rosa_monitoring_preservation':True})
        var=root/'vars.json';R.M.save(var,{})
        snapshot=root/'snapshot.json';R.M.save(snapshot,{'Version':'2012-10-17','Statement':[]})
        accepted={'policy_file':str(snapshot)};c={'create':{'var_file':str(var)}};manifest={'account_id':ACCOUNT}
        p,expected,before=plan();s=scope()
        a=argparse.Namespace(reconstruction_receipt=str(recon/'receipt.json'),scope_inputs='/scope',snapshot_permission_run=str(old),
            restore_hcl=str(Path(__file__).with_name('rosa_monitoring_restore.tf')),apply_exact=True)
        if not Path(a.restore_hcl).is_file():a.restore_hcl=str(Path(__file__).parent.parent/'terraform/bootstrap/rosa_monitoring_restore.tf')
        class FakeRun:
            instance=None
            def __init__(self,folder):self.env={};self.out=folder;self.applies=0;self.pulls=0;self.pregrant_reads=0;FakeRun.instance=self
            def aws(self,args,missing=False):
                if args[:2]==['sts','get-caller-identity']:return {'Account':ACCOUNT,'Arn':'arn:aws:sts::'+ACCOUNT+':assumed-role/seokpan-tf-bootstrap/test'}
                if args[:2]==['iam','get-policy']:
                    if not self.applies:
                        self.pregrant_reads+=1;raise RuntimeError('IAM_READ_FAILED_NO_FALLBACK')
                    return {'Policy':{'DefaultVersionId':'v1'}}
                if args[:2]==['iam','list-attached-role-policies']:return {'AttachedPolicies':[{'PolicyArn':'arn:aws:iam::'+ACCOUNT+':policy/'+R.NAME}] if self.applies or already_attached else []}
                if args[:2]==['iam','list-role-policies']:return {'PolicyNames':['seokpan-tf-bootstrap']}
                if args[:2]==['iam','get-role-policy']:return {'PolicyDocument':R.augment(before,ACCOUNT) if self.applies else before}
                if args[:2]==['iam','get-policy-version']:return {'PolicyVersion':{'Document':expected}}
                raise AssertionError(args)
            def command(self,args,codes=(0,)):
                if 'version' in args:d={'terraform_version':'1.16.4'}
                elif 'init' in args:
                    folder=Path(self.env['TF_DATA_DIR']);folder.mkdir();R.M.save(folder/'terraform.tfstate',{'backend':{'type':'s3','config':{
                        'key':'phase2/bootstrap/terraform.tfstate','bucket':'seokpan-tfstate-'+ACCOUNT,'region':'ap-northeast-2'}}});d={}
                elif 'state' in args:
                    self.pulls+=1;d={'lineage':'fixture','serial':2 if race and self.pulls>1 else 1}
                    if state_existing:d['resources']=[{'mode':'managed','type':'aws_iam_policy','name':'tf_rosa_monitoring_restore','instances':[{'attributes':{'name':R.NAME}}]}]
                elif 'plan' in args:
                    Path(next(x.split('=',1)[1] for x in args if x.startswith('-out='))).write_bytes(b'fixture');d={}
                elif 'show' in args:d=p
                elif 'apply' in args:
                    self.applies+=1
                    if unknown:raise RuntimeError('COMMAND_OUTCOME_UNKNOWN_NO_RETRY')
                    if collision:raise RuntimeError('AWS_ENTITY_ALREADY_EXISTS_NO_TAKEOVER')
                    d={}
                else:raise AssertionError(args)
                return subprocess.CompletedProcess(args,0,json.dumps(d).encode(),b'')
        pins={name:R.M.sha(source/name) for name in ('iam.tf','.terraform.lock.hcl')}
        with patch.object(R,'inputs',return_value=(accepted,c,manifest,s,expected)),patch.object(R.M,'Run',FakeRun),patch.object(R.M,'source_files',return_value=source),patch.object(R.M,'SOURCE_SHA256',pins),patch.object(R.M,'REVIEWED_SAVED_INPUTS',{'monitoring-scope.tfvars.json':R.M.sha(scopefile)}),patch.object(R.M,'previous_settings',return_value={}):
            try:result=R.apply_policy(a,out)
            except RuntimeError as e:result=str(e)
        return result,FakeRun.instance,recon
    def test_exact_plan_applies_once_and_is_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,run,recon=self.apply_fixture(tmp)
            self.assertEqual(r['status'],'PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED');self.assertEqual(run.applies,1)
            self.assertTrue((recon/'restore-policy-accepted.json').is_file())
    def test_state_race_stops_before_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,run,_=self.apply_fixture(tmp,race=True)
            self.assertEqual(r,'STATE_SOURCE_OR_INPUT_CHANGED_NO_APPLY');self.assertEqual(run.applies,0)
    def test_unknown_apply_preserves_marker_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,run,recon=self.apply_fixture(tmp,unknown=True)
            self.assertEqual(r,'COMMAND_OUTCOME_UNKNOWN_NO_RETRY');self.assertEqual(run.applies,1)
            self.assertTrue((recon/'restore-policy-apply.NO_AUTOMATIC_RETRY.json').is_file())

class ReadGrantOrdering(unittest.TestCase):
    def fixture(self,*args,**kwargs):
        return RestorePermissions().apply_fixture(*args,**kwargs)
    def test_missing_pregrant_read_is_not_called_and_postapply_readback_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,_=self.fixture(tmp)
            self.assertEqual(result['status'],'PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED')
            self.assertEqual(run.pregrant_reads,0);self.assertEqual(run.applies,1)
            self.assertFalse(result['read_denial_treated_as_absence'])
    def test_state_owned_policy_stops_before_plan_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,_=self.fixture(tmp,state_existing=True)
            self.assertEqual(result,'RESTORE_POLICY_ALREADY_IN_STATE_REQUIRES_RECONCILIATION')
            self.assertEqual(run.applies,0);self.assertFalse((run.out/'saved.tfplan').exists())
    def test_existing_attachment_stops_without_denied_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,_=self.fixture(tmp,already_attached=True)
            self.assertEqual(result,'RESTORE_POLICY_ALREADY_ATTACHED_REQUIRES_RECONCILIATION')
            self.assertEqual(run.applies,0);self.assertEqual(run.pregrant_reads,0)
    def test_unmanaged_name_collision_preserves_marker_no_acceptance_or_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,run,recon=self.fixture(tmp,collision=True)
            self.assertEqual(result,'AWS_ENTITY_ALREADY_EXISTS_NO_TAKEOVER');self.assertEqual(run.applies,1)
            self.assertTrue((recon/'restore-policy-apply.NO_AUTOMATIC_RETRY.json').is_file())
            self.assertFalse((recon/'restore-policy-accepted.json').exists())
    def test_same_arn_under_other_state_address_is_not_adopted(self):
        arn='arn:aws:iam::'+ACCOUNT+':policy/'+R.NAME
        for kind,attrs in [('aws_iam_policy',{'arn':arn}),('aws_iam_role_policy_attachment',{'policy_arn':arn})]:
            state={'resources':[{'mode':'managed','type':kind,'name':'other','instances':[{'attributes':attrs}]}]}
            with self.assertRaisesRegex(RuntimeError,'ALREADY_IN_STATE'):
                R.require_create_only_state(state,arn)
    def test_source_requires_bootstrap_grant_before_managed_policy_creation(self):
        path=Path(__file__).with_name('rosa_monitoring_restore.tf')
        if not path.is_file():path=Path(__file__).parent.parent/'terraform/bootstrap/rosa_monitoring_restore.tf'
        policy=path.read_text().split('resource "aws_iam_policy" "tf_rosa_monitoring_restore"',1)[1]
        self.assertIn('depends_on = [aws_iam_role_policy.tf_bootstrap]',policy)

if __name__=='__main__':unittest.main()
