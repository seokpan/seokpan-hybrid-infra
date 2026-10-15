import base64
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

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
        self.assertNotIn('activeDeadlineSeconds', p)
        self.assertEqual(p['volumes'], [{'name': 'rds-ca', 'configMap': {'name': 'backend-database-ca'}}])
        self.assertNotIn('env', p['containers'][0])
        self.assertEqual(p['containers'][0]['command'], m.WAIT_COMMAND)
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
                  {'server': 'https://fixture', 'certificate-authority': '/private/ca.pem'}):
            with self.assertRaises(RuntimeError):
                m.limited_config({'clusters': [{'cluster': c}]}, 'https://fixture', 'token')

    def test_system_trust_is_not_insecure(self):
        cfg = m.limited_config({'clusters': [{'cluster': {'server': 'https://fixture'}}]}, 'https://fixture', 'scoped')
        self.assertEqual(cfg['clusters'][0]['cluster'], {'server': 'https://fixture'})
        self.assertNotIn('insecure-skip-tls-verify', json.dumps(cfg))

    def test_tls_server_name_preserved(self):
        cfg = m.limited_config({'clusters': [{'cluster': {'server': 'https://fixture', 'tls-server-name': 'fixture'}}]},
                               'https://fixture/', 'scoped')
        self.assertEqual(cfg['clusters'][0]['cluster']['tls-server-name'], 'fixture')

    def test_twelve_hour_token_expiry(self):
        body = base64.urlsafe_b64encode(json.dumps({'exp': 44200}).encode()).decode().rstrip('=')
        self.assertEqual(m.token_expiry('h.' + body + '.sig', 1000), 44200)

    def test_server_shorter_expiry_accepted_and_reported(self):
        body = base64.urlsafe_b64encode(json.dumps({'exp': 4600}).encode()).decode().rstrip('=')
        self.assertEqual(m.token_expiry('h.' + body + '.sig', 1000), 4600)

    def test_excessive_or_expiring_token_rejected(self):
        for expiry in (1001, 1000 + 86400):
            body = base64.urlsafe_b64encode(json.dumps({'exp': expiry}).encode()).decode().rstrip('=')
            with self.assertRaises(RuntimeError):
                m.token_expiry('h.' + body + '.sig', 1000)

    def reuse(self, old=True, modify=None):
        name = 'data-admin-327da1ec88'
        docs = m.objects(name, 'approved-image', '327da1ec88')
        if old:
            docs[3]['spec']['activeDeadlineSeconds'] = 3600
            docs[3]['spec']['containers'][0]['command'] = ['python', '-u', '-c', 'import time; time.sleep(3550)']
        docs[3]['metadata'] = {**docs[3]['metadata'], 'uid': 'fixture-old-uid'}
        docs[3]['status'] = {'phase': 'Running'}
        docs[0]['imagePullSecrets'].append({'name': name + '-dockercfg-example'})
        if modify:
            modify(docs)
        r = Mock()
        r.get.side_effect = docs
        with tempfile.TemporaryDirectory() as temp:
            result = {}
            m.ensure_reused_objects(r, name, 'approved-image', Path(temp), result)
            return r, result

    def test_old_idle_pod_only_is_replaced(self):
        r, result = self.reuse()
        self.assertEqual(result['access_objects_reused'], 3)
        self.assertTrue(result['old_one_hour_pod_replaced'])
        self.assertEqual(r.call.call_count, 2)
        self.assertEqual(r.call.call_args_list[0].args[0][:3], ['delete', 'pod', 'data-admin-327da1ec88'])
        created = json.loads(r.call.call_args_list[1].args[2])
        self.assertEqual(created['kind'], 'Pod')
        self.assertNotIn('activeDeadlineSeconds', created['spec'])
        self.assertEqual(created['spec']['containers'][0]['command'], m.WAIT_COMMAND)

    def test_renewal_reuses_pod_and_all_permissions(self):
        r, result = self.reuse(old=False)
        self.assertTrue(result['pod_reused'])
        r.call.assert_not_called()

    def test_changed_role_rejected_before_pod_change(self):
        def modify(docs):
            docs[1]['rules'][0]['verbs'].append('delete')
        with self.assertRaisesRegex(RuntimeError, 'PERMISSION_CHANGED'):
            self.reuse(modify=modify)

    def test_changed_owner_rejected(self):
        def modify(docs):
            docs[0]['metadata']['labels'] = {'seokpan.io/data-access': 'other'}
        with self.assertRaisesRegex(RuntimeError, 'OWNER_CHANGED'):
            self.reuse(modify=modify)

    def test_changed_image_or_command_rejected(self):
        def modify(docs):
            docs[3]['spec']['containers'][0]['command'] = ['application-server']
        with self.assertRaisesRegex(RuntimeError, 'COMMAND_CHANGED'):
            self.reuse(modify=modify)

    def test_resume_receipt_and_renewal_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            folder = root / 'data-operator-access-fixture'
            folder.mkdir()
            receipt = {'namespace': m.NS, 'linux_recipient': 'ansible', 'human_recipient': 'A',
                       'sql_executed': False, 'app_started': False, 'pod': 'data-admin-327da1ec88',
                       'status': 'STOP_DATA_OPERATOR_ACCESS', 'reason': 'VERIFIED_CLUSTER_CA_REQUIRED'}
            (folder / 'receipt.json').write_text(json.dumps(receipt))
            self.assertEqual(m.accepted_receipt(root, folder, False)['pod'], receipt['pod'])
            with self.assertRaisesRegex(RuntimeError, 'ACCEPTED_LONG_RUNNING_ACCESS'):
                m.accepted_receipt(root, folder, True)
            receipt.update(status='PASS_A_SINGLE_POD_ACCESS_PREPARED', persistent_until_cleanup=True)
            (folder / 'receipt.json').write_text(json.dumps(receipt))
            self.assertEqual(m.accepted_receipt(root, folder, True)['pod'], receipt['pod'])

    def test_outside_resume_folder_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaisesRegex(RuntimeError, 'RESUME_FOLDER_SCOPE'):
                m.accepted_receipt(root, root / 'other', False)


if __name__ == '__main__':
    unittest.main()
