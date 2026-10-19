"""Only new offline package-to-measurement integration boundaries."""
import copy
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bind_inputs import bind, load_launch
from timeline import sha


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.ns = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        self.app = '11111111-2222-3333-4444-555555555555'
        self.c = {'run_id': 'new-run', 'cluster_id': 'newcluster012345', 'namespace_uid': self.ns,
                  'api_url': 'https://api.example.openshiftapps.com:6443', 'database_host': 'db.example.test',
                  'window': {'not_before': '2026-10-19T00:00:00Z', 'not_after': '2026-10-19T02:00:00Z'}}
        self.source = {'repoURL': 'https://example.test/source', 'targetRevision': 'a'*40}
        self.desired = {'items': [{'kind': 'Deployment', 'metadata': {'name': name},
                       'spec': {'replicas': count, 'template': {'spec': {'containers': [{'image': 'example.test/'+name+'@sha256:'+char*64}]}}}}
                       for name, count, char in [('backend', 3, 'a'), ('frontend', 3, 'b')]]}
        self.plan = {'run_id':'normal', 'runtime':{'replicas':{'backend':3,'frontend':3}},
                     'release':{'backend_digest':'sha256:'+'a'*64,'frontend_digest':'sha256:'+'b'*64}, 'window': self.c['window']}
        self.paths = json.loads((Path(__file__).parent/'execution-paths.TEMPLATE.NOT_ACCEPTED.json').read_text())
        self.paths.update(controller_python='/usr/bin/python3', controller_measurement_dir='/repo/tools/presentation-measurement',
                          controller_work='/private/bound', controller_trigger_prefix=[], observer_node='node',
                          observer_playwright_module='/existing/playwright/index.mjs', db_mariadb='/usr/bin/mariadb',
                          db_credential_file='/private/client.cnf', db_ca_file='/private/ca.pem', db_target_reference='existing-A-receipt')
        self.controller = {'oc':'/existing/oc', 'personal_kubeconfig':'/private/kubeconfig'}
        self.ready = {'application_uid':self.app, 'source_sha256':sha(self.source)}
        self.launch = SimpleNamespace(OLD_CLUSTER='deletedcluster', NS='seokpan-cloud', APP='seokpan-cloud-app',
            package=lambda *a:({},self.c,self.source,self.desired,{}), measurement=lambda *a:copy.deepcopy(self.plan),
            digest=sha, normalize_source=lambda x:x)

    def run_bind(self):
        return bind(self.launch, Path('existing-package'), 'target', self.ready, 'existing-business-receipt', self.paths, self.controller)

    def test_merged_launch_module_is_compatible_without_cluster_or_sql(self):
        root = Path(__file__).resolve().parents[2]
        with patch('subprocess.run', side_effect=AssertionError('NO_COMMAND_REQUIRED')):
            launch = load_launch(root)
        self.assertEqual(launch.APP, 'seokpan-cloud-app')
        self.assertTrue(callable(launch.measurement))

    def test_binds_scenarios_roll_command_db_and_shared_identity_without_reading_credentials(self):
        with patch('subprocess.run', side_effect=AssertionError('NO_RUNTIME_REQUIRED')):
            result = self.run_bind()
        self.assertEqual(result['baseline.json']['binding'], result['rolling.json']['binding'])
        self.assertNotEqual(result['baseline.json']['run_id'], result['rolling.json']['run_id'])
        self.assertEqual(result['rolling-config.json']['expected_replicas'], {'backend':3,'frontend':3})
        self.assertEqual(result['trigger.json']['argv'][-5:], ['--config','/private/bound/rolling-config.json','--work','/private/bound/rolling-work','--execute'])
        self.assertFalse(result['binding-receipt.json']['db_credentials_read'])
        self.assertEqual(result['binding-receipt.json']['missing_paths'], [])
        self.assertEqual(result['db-baseline.json']['credential_file'], '/private/client.cnf')
        self.assertNotEqual(result['db-baseline.json']['binding']['run_id'], result['db-rolling.json']['binding']['run_id'])

    def test_missing_remote_route_is_pending_never_invents_ssh(self):
        self.paths['controller_trigger_prefix'] = None
        result = self.run_bind()
        self.assertEqual(result['trigger.json']['argv'], [])
        self.assertIn('controller_trigger_prefix', result['binding-receipt.json']['missing_paths'])

    def test_unknown_application_uid_and_wrong_ready_source_stop(self):
        self.ready['application_uid'] = None
        with self.assertRaises((RuntimeError, TypeError)): self.run_bind()
        self.ready['application_uid'] = self.app
        self.ready['source_sha256'] = '0'*64
        with self.assertRaisesRegex(RuntimeError, 'SAME_SOURCE_READY'): self.run_bind()

    def test_deleted_target_or_mismatched_image_cannot_be_adopted(self):
        self.c['cluster_id'] = self.launch.OLD_CLUSTER
        with self.assertRaisesRegex(RuntimeError, 'DELETED_CLUSTER'): self.run_bind()
        self.c['cluster_id'] = 'newcluster012345'
        self.plan['release']['backend_digest'] = 'sha256:'+'c'*64
        with self.assertRaisesRegex(RuntimeError, 'IMAGE_REPLICA_MISMATCH'): self.run_bind()

    def test_trigger_string_and_shell_path_are_not_connected(self):
        self.paths['controller_trigger_prefix'] = 'ssh host'
        with self.assertRaisesRegex(RuntimeError, 'ARGV_PREFIX_ARRAY'): self.run_bind()
        self.paths['controller_trigger_prefix'] = []
        self.paths['controller_work'] = '/private/path;touch-elsewhere'
        with self.assertRaisesRegex(RuntimeError, 'WITHOUT_SHELL'): self.run_bind()

    def test_unbounded_collector_or_extra_recovery_is_rejected(self):
        self.paths['db_duration_seconds'] = 3600
        with self.assertRaisesRegex(RuntimeError, 'BOUNDED_DB'): self.run_bind()
        self.paths['db_duration_seconds'] = 600
        self.paths['extra_recovery_seconds'] = 121
        with self.assertRaisesRegex(RuntimeError, 'BOUNDED_EXTRA'): self.run_bind()

    def test_unknown_input_fields_do_not_copy_credentials_into_results(self):
        self.paths['password'] = 'synthetic-only-never-adopt'
        with self.assertRaisesRegex(RuntimeError, 'EXACT_PATH_INPUT'): self.run_bind()


if __name__ == '__main__': unittest.main()
