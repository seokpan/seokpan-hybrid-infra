"""New denial/classification/restore-resume boundaries; never calls cloud APIs."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def module(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
P=module('prepare-rosa-reconstruction')
E=module('rosa-lifecycle-entry')
R=module('rosa-held-restoration')
C={'cloud_namespace':'cloud','argo_namespace':'argo','argo_name':'instance',
   'groups':['operators'],'applications':['app'],'projects':['app'],'expected_api':'https://api.old.openshiftapps.com:6443'}
def obj(kind,name,ns=None,**fields):
    m={'name':name}
    if ns:m['namespace']=ns
    return {'apiVersion':'v1','kind':kind,'metadata':m,**fields}
def private(path,value):
    path.write_text(json.dumps(value));path.chmod(0o600);return path


class ReconstructionBoundaries(unittest.TestCase):
    def test_existing_log_classifies_exact_action_without_api_or_raw_arn(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);private(d/'receipt.json',{'status':'STOP_MONITORING_SNAPSHOT_CAPTURE'})
            log=d/'aws-failure-private.log'
            log.write_text('An error occurred (UnauthorizedOperation) when calling the DescribeVolumes operation: private account details; because no identity-based policy allows the ec2:DescribeVolumes action')
            log.chmod(0o600)
            with patch.object(E.subprocess,'run') as calls:
                value=P.failure_diagnostic(d,d)
                calls.assert_not_called()
            self.assertEqual(value['failed_aws_action'],'ec2:DescribeVolumes')
            self.assertEqual(value['authorization_class'],'IDENTITY_POLICY_ALLOW_MISSING')
            self.assertIs(value['this_tool_non_dry_run_snapshot_create_invoked'],False)
            self.assertNotIn('private account details',json.dumps(value))
    def test_marker_prevents_treating_unknown_creation_as_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);private(d/'receipt.json',{'status':'STOP_MONITORING_SNAPSHOT_CAPTURE'})
            private(d/'monitoring-snapshot-attempt.NO_AUTOMATIC_RETRY.json',{})
            log=d/'aws-failure-private.log';log.write_text('explicit deny when calling the CreateSnapshot operation');log.chmod(0o600)
            value=P.failure_diagnostic(d,d)
            self.assertEqual(value['this_tool_non_dry_run_snapshot_create_invoked'],'RECONCILIATION_REQUIRED')
            self.assertEqual(value['authorization_class'],'EXPLICIT_DENY_OWNER_REVIEW_NO_BYPASS')
    def test_ten_previously_unclassified_objects_have_deliberate_actions(self):
        rows=[obj('ServiceMonitor','monitor','cloud'),obj('PrometheusRule','rules','cloud'),
              obj('AppProject','app','argo'),obj('AppProject','app','argo'),
              *[obj('PersistentVolume','pv-'+str(i)) for i in range(4)],
              obj('StatefulSet','alertmanager-main','openshift-monitoring'),
              obj('StatefulSet','prometheus-k8s','openshift-monitoring')]
        actions=[P.classify(o,C) for o in rows]
        self.assertEqual(actions.count('SNAPSHOT_BACKED_NEW_VOLUME_PV_PVC'),4)
        self.assertEqual(actions.count('MONITORING_OPERATOR_REGENERATION'),2)
        self.assertNotIn('REVIEW_BEFORE_REPLAY',actions)
    def test_expired_pull_and_old_ca_are_never_reapplied(self):
        self.assertEqual(P.classify(obj('Secret','seokpan-cloud-ecr-pull','cloud'),C),'ARCHIVE_ONLY_EXPIRED_PULL_NEVER_REATTACH')
        self.assertEqual(P.classify(obj('ConfigMap','kube-root-ca.crt','cloud'),C),'NEW_CLUSTER_CA_REGENERATION')
        with self.assertRaisesRegex(RuntimeError,'UNREVIEWED_MANUAL_SECRET'):
            P.classify(obj('Secret','unknown','cloud'),C)
    def test_no_automatic_application_or_active_workload_enters_recipe(self):
        for o in (obj('Deployment','backend','cloud',spec={'replicas':1}),
                  obj('Application','app','argo',spec={'syncPolicy':{'automated':{}}})):
            with self.assertRaises(RuntimeError):P.classify(o,C)
    def test_old_scc_uids_ownerreferences_tokens_and_pull_refs_are_stripped(self):
        value=obj('ServiceAccount','runtime','cloud',secrets=[{'name':'old-token'}],
                  imagePullSecrets=[{'name':'seokpan-cloud-ecr-pull'}])
        value['metadata'].update(uid='old',resourceVersion='9',ownerReferences=[{}],
            annotations={'openshift.io/sa.scc.uid-range':'old','seokpan.io/owner':'preserved'})
        clean=P.normalized_metadata(value)
        self.assertNotIn('uid',clean['metadata']);self.assertNotIn('secrets',clean)
        self.assertNotIn('imagePullSecrets',clean)
        self.assertEqual(clean['metadata']['annotations'],{'seokpan.io/owner':'preserved'})
    def test_snapshot_policy_has_exact_four_encrypted_sources_no_delete_or_iam(self):
        rows=[{'volume_id':'vol-'+str(i),'claim_uid':'claim-'+str(i),'namespace':'openshift-monitoring'} for i in range(4)]
        value=P.snapshot_policy('123456789012',rows,'a'*64)
        statement=value['Statement'];self.assertEqual(len(statement[1]['Resource']),4)
        self.assertEqual(statement[2]['Resource'],'arn:aws:ec2:ap-northeast-2::snapshot/*')
        self.assertEqual(statement[3]['Condition']['StringEquals']['ec2:CreateAction'],'CreateSnapshot')
        self.assertNotRegex(json.dumps(value),r'Delete|CreateVolume|iam:|kms:\*|ec2:\*')
        with self.assertRaises(RuntimeError):P.snapshot_policy('123456789012',rows[:3],'a'*64)
    def test_exact_source_is_materialized_without_old_state_or_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);files={'main.tf':b'terraform {}','.terraform.lock.hcl':b'fixture'}
            rows=[{'root':'/old/terraform/rosa','path':name,'data':base64.b64encode(raw).decode(),
                   'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in {**files,'before.tfstate':b'private'}.items()]
            hashvalue=hashlib.sha256('\n'.join(name+':'+hashlib.sha256(files[name]).hexdigest() for name in sorted(files)).encode()).hexdigest()
            target=P.materialize_adopted_source({'files':rows},{'tf_root':'/old/terraform/rosa','source_hash':hashvalue},d)
            self.assertEqual({x.name for x in target.iterdir()},set(files))
    def test_new_identity_rejects_wrong_api_or_creator_before_mutations(self):
        h={'cluster_id':'new','api_url':'https://api.new.openshiftapps.com:6443'}
        observed={'id':'new','state':'ready','api':{'url':h['api_url']},'region':{'id':'ap-northeast-2'},
                  'properties':{'rosa_creator_arn':'arn:aws:sts::123456789012:assumed-role/seokpan-tf-rosa/test'}}
        R.validate_new_identity(observed,h,'123456789012')
        observed['api']['url']='https://old.invalid'
        with self.assertRaisesRegex(RuntimeError,'ID_API_REGION_CREATOR'):R.validate_new_identity(observed,h,'123456789012')
    def test_restore_resume_never_prepares_or_applies_cluster(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);c={'create':{'last_platform_handoff':str(d/'h.json')}}
            with patch.object(E,'restore',return_value={'status':'fixture'}) as restored,patch.object(E.L,'prepare') as plan,patch.object(E.L,'apply') as apply:
                self.assertEqual(E.run(c,'restore',d,d)['status'],'fixture')
                restored.assert_called_once();plan.assert_not_called();apply.assert_not_called()
    def test_completed_stage_reuse_is_cluster_and_recipe_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);r=R.Restore({'auth':{'oc_binary':'unused','rosa_cli':'unused'},'restoration':{'recipe_sha256':'a'*64}},
                {'cluster_id':'new'},{'objects':[],'decisions':[]},d)
            private(d/'fixture.complete.json',{'cluster_id':'old','recipe_sha256':'a'*64,'result':{}})
            with self.assertRaisesRegex(RuntimeError,'COMPLETED_STAGE_BINDING_CHANGED'):r.stage('fixture',lambda:self.fail('must not run'))
    def test_partial_stage_blocks_automatic_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);r=R.Restore({'auth':{'oc_binary':'unused','rosa_cli':'unused'}},{'cluster_id':'new'},
                {'objects':[],'decisions':[]},d)
            private(d/'fixture.attempt.json',{})
            with self.assertRaisesRegex(RuntimeError,'PARTIAL_STAGE_REQUIRES_READ_ONLY'):r.stage('fixture',lambda:self.fail('must not run'))
    def test_application_empty_group_normalization_does_not_hide_other_differences(self):
        a={'source':{'kustomize':{'patches':[{'target':{'kind':'Deployment','group':''},'patch':'[]'}]}}}
        b={'source':{'kustomize':{'patches':[{'target':{'kind':'Deployment'},'patch':[]}]}}}
        self.assertEqual(R.application_spec(a),R.application_spec(b))
        b['source']['kustomize']['patches'][0]['target']['kind']='Service'
        self.assertNotEqual(R.application_spec(a),R.application_spec(b))
    def test_new_host_mapping_preserves_secret_payload_bytes(self):
        recipe={'contract':C,'objects':[]}
        value=obj('Secret','backend-db-runtime','cloud',data={'value':'old-base64'})
        self.assertEqual(R.desired(value,recipe,{'api_url':'https://api.new.openshiftapps.com:6443'})['data'],value['data'])


if __name__=='__main__':unittest.main()
