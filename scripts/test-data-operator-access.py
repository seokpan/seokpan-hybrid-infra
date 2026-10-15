import base64
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

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

    def reuse(self, old=True, modify=None, expected_uid='fixture-old-uid', renewal=False):
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
        r.get.side_effect = docs + ([None] if old else [])
        created = copy.deepcopy(m.objects(name, 'approved-image', '327da1ec88')[3])
        created['metadata']['uid'] = 'fixture-new-uid'
        r.call.return_value = SimpleNamespace(stdout=json.dumps(created).encode())
        with tempfile.TemporaryDirectory() as temp:
            result = {}
            m.ensure_reused_objects(r, name, 'approved-image', Path(temp), result,
                                    expected_uid=expected_uid, renewal=renewal)
            return r, result

    def test_old_idle_pod_only_is_replaced(self):
        r, result = self.reuse()
        self.assertEqual(result['access_objects_reused'], 3)
        self.assertTrue(result['old_one_hour_pod_replaced'])
        self.assertEqual(r.call.call_count, 2)
        self.assertEqual(r.call.call_args_list[0].args[0], ['delete',
            '--raw=/api/v1/namespaces/seokpan-cloud/pods/data-admin-327da1ec88', '-f', '-'])
        self.assertEqual(json.loads(r.call.call_args_list[0].args[2])['preconditions'], {'uid': 'fixture-old-uid'})
        self.assertEqual(result['pod_uid'], 'fixture-new-uid')
        created = json.loads(r.call.call_args_list[1].args[2])
        self.assertEqual(created['kind'], 'Pod')
        self.assertNotIn('activeDeadlineSeconds', created['spec'])
        self.assertEqual(created['spec']['containers'][0]['command'], m.WAIT_COMMAND)

    def test_renewal_reuses_pod_and_all_permissions(self):
        r, result = self.reuse(old=False, renewal=True)
        self.assertTrue(result['pod_reused'])
        self.assertEqual(result['pod_uid'], 'fixture-old-uid')
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
            receipt.update(status='PASS_A_SINGLE_POD_ACCESS_PREPARED', persistent_until_cleanup=True, pod_uid='fixture-uid')
            (folder / 'receipt.json').write_text(json.dumps(receipt))
            self.assertEqual(m.accepted_receipt(root, folder, True)['pod'], receipt['pod'])

    def test_outside_resume_folder_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaisesRegex(RuntimeError, 'RESUME_FOLDER_SCOPE'):
                m.accepted_receipt(root, root / 'other', False)

    def test_different_uid_same_name_label_spec_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'UID_CHANGED'):
            self.reuse(old=False, renewal=True, expected_uid='original-reviewed-uid')

    def test_missing_uid_never_adopts_existing_live_pod(self):
        with self.assertRaisesRegex(RuntimeError, 'UID_CHANGED'):
            self.reuse(old=False, renewal=True, expected_uid=None)

    def test_renewal_missing_pod_never_recreates(self):
        def modify(docs):
            docs[3] = None
        with self.assertRaisesRegex(RuntimeError, 'RENEWAL_POD_MISSING_NO_RECREATE'):
            self.reuse(old=False, modify=modify, renewal=True)

    def test_renewal_never_replaces_short_old_command(self):
        with self.assertRaisesRegex(RuntimeError, 'COMMAND_CHANGED'):
            self.reuse(old=True, renewal=True)

    def test_security_field_changes_rejected(self):
        mutators = {
            'privileged': lambda p: p['spec']['containers'][0]['securityContext'].update(privileged=True),
            'hostNetwork': lambda p: p['spec'].update(hostNetwork=True),
            'hostPID': lambda p: p['spec'].update(hostPID=True),
            'hostIPC': lambda p: p['spec'].update(hostIPC=True),
            'init': lambda p: p['spec'].update(initContainers=[{'name': 'extra'}]),
            'ephemeral': lambda p: p['spec'].update(ephemeralContainers=[{'name': 'extra'}]),
            'nodeSelector': lambda p: p['spec'].pop('nodeSelector'),
            'affinity': lambda p: p['spec'].pop('affinity'),
            'rootMount': lambda p: p['spec']['containers'][0]['volumeMounts'][0].update(mountPath='/'),
            'writableMount': lambda p: p['spec']['containers'][0]['volumeMounts'][0].update(readOnly=False),
            'terminating': lambda p: p['metadata'].update(deletionTimestamp='2026-10-15T00:00:00Z'),
            'deadline': lambda p: p['spec'].update(activeDeadlineSeconds=3600),
            'root': lambda p: p['spec']['securityContext'].update(runAsNonRoot=False),
            'seccomp': lambda p: p['spec']['securityContext'].update(seccompProfile={'type': 'Unconfined'}),
            'escalation': lambda p: p['spec']['containers'][0]['securityContext'].update(allowPrivilegeEscalation=True),
            'capabilities': lambda p: p['spec']['containers'][0]['securityContext']['capabilities'].update(add=['SYS_ADMIN']),
        }
        for name, mutate in mutators.items():
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                self.reuse(old=False, renewal=True, modify=lambda docs: mutate(docs[3]))

    def test_admission_nonroot_uid_and_selinux_defaults_accepted(self):
        def modify(docs):
            docs[3]['spec']['securityContext'].update(runAsUser=1001000000, fsGroup=1001000000,
                                                     seLinuxOptions={'level': 'fixture'})
            docs[3]['spec']['volumes'][0]['configMap']['defaultMode'] = 420
        r, result = self.reuse(old=False, renewal=True, modify=modify)
        r.call.assert_not_called()
        self.assertTrue(result['pod_reused'])

    def test_delete_race_uid_precondition_rejects_new_pod(self):
        r = Mock()
        def reject(args, label, payload):
            options = json.loads(payload)
            self.assertEqual(options['preconditions'], {'uid': 'old-uid'})
            live_uid = 'replacement-uid'
            if options['preconditions']['uid'] != live_uid:
                raise RuntimeError('DELETE_UID_PRECONDITION_CONFLICT')
        r.call.side_effect = reject
        with self.assertRaisesRegex(RuntimeError, 'PRECONDITION_CONFLICT'):
            m.delete_exact_idle_pod(r, 'data-admin-327da1ec88', 'old-uid')
        r.get.assert_not_called()

    def test_name_reuse_after_successful_delete_stops_before_create(self):
        r = Mock()
        r.get.return_value = {'metadata': {'uid': 'replacement-uid'}}
        with self.assertRaisesRegex(RuntimeError, 'NAME_REUSED_AFTER_DELETE'):
            m.delete_exact_idle_pod(r, 'data-admin-327da1ec88', 'old-uid')
        self.assertEqual(r.call.call_count, 1)

    def test_legacy_success_receipt_without_uid_refused_for_renewal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); folder = root / 'data-operator-access-fixture'; folder.mkdir()
            receipt = {'namespace': m.NS, 'linux_recipient': 'ansible', 'human_recipient': 'A',
                       'sql_executed': False, 'app_started': False, 'pod': 'data-admin-327da1ec88',
                       'status': 'PASS_A_SINGLE_POD_ACCESS_PREPARED', 'persistent_until_cleanup': True}
            (folder / 'receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(RuntimeError, 'NO_LEGACY_REBIND'):
                m.accepted_receipt(root, folder, True)


if __name__ == '__main__':
    unittest.main()
