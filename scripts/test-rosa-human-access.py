import copy
import importlib.util
from pathlib import Path
import unittest
import tempfile
import json

source=Path(__file__).with_name('configure-rosa-human-access.py')
if not source.is_file(): source=Path(__file__).with_name('configure-rosa-human-access-20261015.py')
spec=importlib.util.spec_from_file_location('access',source)
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.user={'metadata':{'name':a.USER,'uid':'test-uid'},'identities':['seokpan-github:123']}
        self.identity={'metadata':{'name':'seokpan-github:123'},'providerName':'seokpan-github',
                       'user':{'name':a.USER,'uid':'test-uid'}}
        self.argo={'metadata':{'resourceVersion':'12'},'spec':{
            'sso':{'provider':'dex','dex':{'openShiftOAuth':True}},
            'rbac':{'defaultPolicy':'','scopes':'[groups]',
                    'policy':'g, system:cluster-admins, role:admin\ng, cluster-admins, role:admin\n'}}}

    def test_valid_actual_identity(self):
        a.require_identity(self.user,self.identity)

    def test_other_person_denied(self):
        self.user['metadata']['name']='other-user'
        with self.assertRaises(a.Stop): a.require_identity(self.user,self.identity)

    def test_uid_mismatch_denied(self):
        self.identity['user']['uid']='different'
        with self.assertRaises(a.Stop): a.require_identity(self.user,self.identity)

    def test_wrong_provider_denied(self):
        self.identity['providerName']='other-provider'
        with self.assertRaises(a.Stop): a.require_identity(self.user,self.identity)

    def test_multiple_identities_denied(self):
        self.user['identities'].append('seokpan-github:456')
        with self.assertRaises(a.Stop): a.require_identity(self.user,self.identity)

    def test_preserves_existing_admin_policy(self):
        old,new=a.candidate_rbac(self.argo)
        self.assertTrue(new['policy'].startswith(old['policy']))
        self.assertEqual(new['scopes'],old['scopes'])
        self.assertEqual(new['defaultPolicy'],'')

    def test_no_duplicate_rules_on_reuse(self):
        _,new=a.candidate_rbac(self.argo)
        self.argo['spec']['rbac']=new
        before,after=a.candidate_rbac(self.argo)
        self.assertEqual(before,after)

    def test_conflicting_owned_rule_denied(self):
        self.argo['spec']['rbac']['policy']+=f'g, {a.GROUP}, role:admin\n'
        with self.assertRaises(a.Stop): a.candidate_rbac(self.argo)

    def test_nonempty_default_denied(self):
        self.argo['spec']['rbac']['defaultPolicy']='role:readonly'
        with self.assertRaises(a.Stop): a.candidate_rbac(self.argo)

    def test_other_sso_denied(self):
        self.argo['spec']['sso']['provider']='keycloak'
        with self.assertRaises(a.Stop): a.candidate_rbac(self.argo)

    def test_gitops_owned_argo_denied(self):
        self.argo['metadata']['annotations']={'argocd.argoproj.io/tracking-id':'another-owner'}
        with self.assertRaises(a.Stop): a.candidate_rbac(self.argo)

    def test_group_other_member_denied(self):
        group={'metadata':{'labels':{a.LABEL:'cloud-gitops'}},'users':[a.USER,'other-user']}
        with self.assertRaises(a.Stop): a.require_group(group)

    def test_group_unowned_denied(self):
        with self.assertRaises(a.Stop): a.require_group({'users':[a.USER]})

    def test_owned_group_reused(self):
        a.require_group({'metadata':{'labels':{a.LABEL:'cloud-gitops'}},'users':[a.USER]})

    def test_patch_compares_version_and_old_policy(self):
        old,new=a.candidate_rbac(self.argo)
        patch=a.patch_for(self.argo,old,new)
        self.assertEqual([r['op'] for r in patch],['test','test','replace'])
        self.assertEqual(patch[0]['value'],'12')
        self.assertEqual(patch[1]['value'],old)
        self.assertEqual(patch[2]['path'],'/spec/rbac')

    def test_exact_cloud_scope_no_global_admin_or_write(self):
        rules=[r.split(', ') for r in a.LINES if r.startswith('p,')]
        self.assertEqual({r[3] for r in rules},{'get','sync'})
        self.assertTrue(all(r[4].startswith(a.PROJECT) for r in rules))
        self.assertNotIn('role:admin','\n'.join(a.LINES))
        self.assertEqual(len(a.LINES),4)

    def test_target_reuses_protected_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            management=root/'management'/'synthetic-cluster'
            (management/'data-binding').mkdir(parents=True)
            (management/'data-binding/receipt.json').write_text(json.dumps({'status':'PASS_DATA_CONNECTION_RULES_APPLIED'}))
            (management/'data-platform-handoff.json').write_text(json.dumps({
                'cluster_id':'synthetic-cluster','binding_enabled':True,
                'api_url':'https://api.synthetic.example.openshiftapps.com:6443'}))
            self.assertEqual(a.accepted_target(root)[0],'synthetic-cluster')

    def test_unaccepted_target_denied(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'management').mkdir()
            with self.assertRaises(a.Stop): a.accepted_target(root)


if __name__=='__main__': unittest.main()
