import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest

p = Path(__file__).with_name('prepare-data-operator-access-20261015.py')
if not p.exists():
    p = Path(__file__).with_name('prepare-data-operator-access.py')
s = importlib.util.spec_from_file_location('access', p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


class AccessChecks(unittest.TestCase):
    def test_exact_named_pod_permissions(self):
        objs = m.objects('data-fixture', 'registry@sha256:fixture', 'fixture')
        rules = objs[1]['rules']
        self.assertEqual(rules, [
            {'apiGroups': [''], 'resources': ['pods'], 'resourceNames': ['data-fixture'], 'verbs': ['get']},
            {'apiGroups': [''], 'resources': ['pods/exec'], 'resourceNames': ['data-fixture'], 'verbs': ['get', 'create']}])

    def test_binding_uses_own_serviceaccount_and_role(self):
        obj = m.objects('data-fixture', 'image', 'fixture')[2]
        self.assertEqual(obj['roleRef']['kind'], 'Role')
        self.assertEqual(obj['roleRef']['name'], 'data-fixture')
        self.assertEqual(obj['subjects'], [{'kind': 'ServiceAccount', 'name': 'data-fixture', 'namespace': 'seokpan-cloud'}])

    def test_pod_has_no_credential_mount_or_app_command(self):
        p = m.objects('data-fixture', 'image', 'fixture')[3]['spec']
        self.assertFalse(p['automountServiceAccountToken'])
        self.assertEqual(p['activeDeadlineSeconds'], 3600)
        self.assertEqual(p['volumes'], [{'name': 'rds-ca', 'configMap': {'name': 'backend-database-ca'}}])
        self.assertNotIn('env', p['containers'][0])
        self.assertIn('sleep(3550)', p['containers'][0]['command'][-1])
        self.assertTrue(p['containers'][0]['securityContext']['readOnlyRootFilesystem'])

    def test_parent_admin_credentials_never_copied(self):
        parent = {'clusters': [{'cluster': {'server': 'https://fixture', 'certificate-authority-data': 'CA'}}],
                  'users': [{'user': {'token': 'PARENT_ADMIN_TOKEN', 'client-key-data': 'PARENT_KEY'}}]}
        result = m.limited_config(parent, 'https://fixture', 'SCOPED_TOKEN')
        self.assertNotIn('PARENT', json.dumps(result))
        self.assertEqual(result['users'][0]['user'], {'token': 'SCOPED_TOKEN'})

    def test_wrong_context_or_insecure_tls_rejected(self):
        for c in ({'server': 'https://other', 'certificate-authority-data': 'CA'},
                  {'server': 'https://fixture', 'certificate-authority-data': 'CA', 'insecure-skip-tls-verify': True},
                  {'server': 'https://fixture'}):
            with self.assertRaises(RuntimeError):
                m.limited_config({'clusters': [{'cluster': c}]}, 'https://fixture', 'token')

    def test_short_token_expiry(self):
        body = base64.urlsafe_b64encode(json.dumps({'exp': 4600}).encode()).decode().rstrip('=')
        self.assertEqual(m.token_expiry('h.' + body + '.sig', 1000), 4600)

    def test_excessive_or_expiring_token_rejected(self):
        for expiry in (1001, 1000 + 7200):
            body = base64.urlsafe_b64encode(json.dumps({'exp': expiry}).encode()).decode().rstrip('=')
            with self.assertRaises(RuntimeError):
                m.token_expiry('h.' + body + '.sig', 1000)


if __name__ == '__main__':
    unittest.main()
