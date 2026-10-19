"""New Source staging failure/continuation boundaries; mocked oc only."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import cloud_launch as L
from test_launch_boundaries import app


@unittest.skipUnless(os.environ.get('LAUNCH_ACCEPTED_PACKAGE'), 'prepared synthetic package not supplied')
class Staging(unittest.TestCase):
    def fixture(self, td):
        package = Path(os.environ['LAUNCH_ACCEPTED_PACKAGE'])
        p, c, source, desired, release = L.package(package, 'single')
        held = td/'held.json'; L.write(held, {'items': [{'kind': 'ConfigMap', 'data': {'SEOKPAN_DATABASE_EXPECTED_HOST': c['database_host']}}]})
        data = {'accepted_by': 'tjung03', 'final_import_compare_and_schema_handoff_accepted': True,
                'source_writer_hold_accepted': True, 'reported_schema_revision': '20260902_0002',
                'final_import_evidence_ref': 'synthetic-final-import', 'source_writer_hold_evidence_ref': 'synthetic-old-stop'}
        target = {'cluster_id': c['cluster_id'], 'namespace_uid': c['namespace_uid'],
                  'secret_uid': c['protected_uids']['secret/backend-db-runtime'],
                  'ca_uid': c['protected_uids']['configmap/backend-database-ca'],
                  'held_bundle': str(held), 'held_bundle_sha256': L.hashlib.sha256(held.read_bytes()).hexdigest()}
        sql = {'status': 'PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION', 'cluster_id': c['cluster_id'], 'new_cluster_bound': True}
        e = {'migration_current': '20260902_0002', 'migration_current_ref': 'synthetic-current',
             'captured_transition_accepted': True, 'captured_transition_ref': 'synthetic-transition', 'pool_candidate_accepted': True}
        for k, v in [('final_data_handoff', data), ('purpose_sql_receipt', sql), ('purpose_sql_target', target)]:
            path = td/(k+'.json'); L.write(path, v); e[k] = str(path)
        kubeconfig = td/'kubeconfig'; kubeconfig.write_text('synthetic-local-config')
        kubeconfig.chmod(0o600)
        state = app(); calls = []
        def runner(args, **kwargs):
            a = args[3:]; calls.append(a)
            if a[:2] == ['config', 'view']:
                value = {'clusters': [{'cluster': {'server': c['api_url']}}]}
            elif a == ['whoami']: return SimpleNamespace(returncode=0, stdout=b'tjung03\n')
            elif a[:2] == ['get', 'namespace']: value = {'metadata': {'uid': c['namespace_uid']}}
            elif a[:2] in (['get', 'secret'], ['get', 'configmap']):
                return SimpleNamespace(returncode=0, stdout=c['protected_uids'][a[1]+'/'+a[2]].encode())
            elif a[:2] == ['get', 'application']: value = state
            elif a[:2] == ['get', 'deployment']: return SimpleNamespace(returncode=0, stdout=b'')
            elif a[:2] == ['patch', 'application']:
                ops = L.read(next(x.split('=', 1)[1] for x in a if x.startswith('--patch-file=')))
                self.assertEqual(ops[0]['path'], '/metadata/uid')
                self.assertEqual(ops[1]['path'], '/metadata/resourceVersion')
                self.assertEqual(ops[2]['value'], state['spec'])
                state['spec']['source'] = ops[-1]['value']; value = state
            else: raise AssertionError(a)
            return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())
        return package, c, e, kubeconfig, runner, calls

    def test_source_only_update_exact_cas_and_console_default_no_sync(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as raw:
            td = Path(raw); package, c, e, kubeconfig, runner, calls = self.fixture(td)
            with patch.object(L, 'budget_check', return_value={'synthetic': True}):
                result = L.stage_app(package, 'single', e, kubeconfig, 'oc', td/'receipt.json', runner)
            self.assertEqual(result['status'], 'PASS_CLOUD_SOURCE_STAGED_NO_SYNC')
            self.assertFalse(result['sync_started'])
            self.assertEqual(sum(a[0] == 'patch' for a in calls), 1)
            self.assertFalse(any(a[0] in ('apply', 'delete', 'exec') for a in calls))
            js = Path(result['console_file']).read_text()
            self.assertIn('const SYNC_NOW = false', js)
            self.assertNotIn('__LAUNCH_BASELINE__', js)

    def test_failed_patch_preserves_marker_and_does_not_retry(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as raw:
            td = Path(raw); package, c, e, kubeconfig, runner, calls = self.fixture(td)
            def failing(args, **kw):
                if args[3] == 'patch':
                    calls.append(['patch', 'synthetic-failure'])
                    return SimpleNamespace(returncode=1, stdout=b'')
                return runner(args, **kw)
            receipt = td/'receipt.json'
            with patch.object(L, 'budget_check', return_value={'synthetic': True}):
                with self.assertRaisesRegex(L.Stop, 'FAILED_NO_RETRY'):
                    L.stage_app(package, 'single', e, kubeconfig, 'oc', receipt, failing)
            self.assertTrue(receipt.with_name(receipt.name+'.STARTED_NO_AUTOMATIC_RETRY.json').exists())
            self.assertEqual(sum(a[0] == 'patch' for a in calls), 1)

    def test_incomplete_data_stops_before_any_oc_call(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as raw:
            td = Path(raw); package, c, e, kubeconfig, runner, calls = self.fixture(td)
            h = Path(e['final_data_handoff']); v = L.read(h); v['source_writer_hold_accepted'] = False
            h.write_text(json.dumps(v))
            with self.assertRaisesRegex(L.Stop, 'FINAL_DATA'):
                L.stage_app(package, 'single', e, kubeconfig, 'oc', td/'receipt.json', runner)
            self.assertEqual(calls, [])

    def test_ready_readback_rejects_stale_compared_source(self):
        package = Path(os.environ['LAUNCH_ACCEPTED_PACKAGE'])
        p, c, source, desired, release = L.package(package, 'single')
        a = app(); a['spec']['source'] = source
        a['status'] = {'sync': {'revision': L.GITOPS, 'status': 'Synced', 'comparedTo': {'source': {'path': 'stale'}}},
                       'health': {'status': 'Healthy'}}
        def call(*args): return json.dumps(a).encode()
        with patch.object(L, 'oc_client', return_value=call):
            with self.assertRaisesRegex(L.Stop, 'NOT_SYNCED_HEALTHY'):
                L.readback(package, 'single', Path('unused'), 'oc')

    def test_measurement_uses_same_config_images_and_stage(self):
        package = Path(os.environ['LAUNCH_ACCEPTED_PACKAGE'])
        p, c, source, desired, release = L.package(package, 'shared')
        ready = {'status': 'PASS_CLOUD_STAGE_READY_NOT_BUSINESS_ACCEPTANCE', 'stage': 'shared',
                 'cluster_id': c['cluster_id'], 'source_sha256': L.digest(source)}
        m = L.measurement(package, 'shared', ready, 'synthetic-business-accepted')
        self.assertEqual(m['runtime']['replicas'], {'backend': 2, 'frontend': 1})
        self.assertEqual(m['runtime']['config_revision'], release['revisions']['config'])
        self.assertEqual(m['release']['backend_digest'], release['images']['backend']['ecr_digest'])
        self.assertEqual(m['profile']['clients'], 6)
        self.assertTrue(m['run_id'].endswith('-shared-normal'))


if __name__ == '__main__': unittest.main()
