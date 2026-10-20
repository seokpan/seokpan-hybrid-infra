"""New receipt-shape boundaries only; no Cloud, SQL or subprocess execution."""
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import types
import unittest

spec = importlib.util.spec_from_file_location('repair', Path(__file__).with_name('resume_schema_observation.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class ReferenceRepair(unittest.TestCase):
    def setUp(self):
        self.binding = {'cluster_id': 'current-cluster', 'input_sha256': 'bound'}
        self.raw = {'status': 'PASS_SCHEMA_AND_CAPTURED_ADMISSION_NOT_BUSINESS',
            'data_mode': 'fresh_test', 'fresh_database_empty_verified': True,
            'schema_revision': '20260902_0002', 'database_writes': 0, 'redis_writes': 0}
        self.wrapper = {'binding': self.binding, 'completed_at_utc': '2026-10-20T08:00:00Z', 'result': self.raw}

    def test_completed_actual_result_is_unwrapped_without_new_probe(self):
        self.assertEqual(M.observation(self.wrapper, self.binding), self.raw)

    def test_wrong_binding_and_non_success_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'BINDING'):
            M.observation(self.wrapper, {'cluster_id': 'old'})
        self.wrapper['result'] = dict(self.raw, database_writes=1)
        with self.assertRaisesRegex(RuntimeError, 'SUCCESSFUL'):
            M.observation(self.wrapper, self.binding)

    def test_only_both_known_reference_fields_can_change(self):
        value = {'migration_current_ref': str(Path('/work/schema-mode.json')),
                 'captured_transition_ref': str(Path('/work/schema-mode.json')), 'other': 'preserved'}
        fixed, changed = M.repaired_evidence(value, Path('/work/schema-mode.json'), Path('/work/raw.json'))
        self.assertTrue(changed)
        self.assertEqual(fixed['other'], value['other'])
        with self.assertRaisesRegex(RuntimeError, 'TWO_KNOWN'):
            M.repaired_evidence(dict(value, captured_transition_ref='/different'),
                                Path('/work/schema-mode.json'), Path('/work/raw.json'))

    def test_actual_launch_validator_rejects_wrapper_and_accepts_same_raw_result(self):
        default = Path(__file__).resolve().parents[2]/'tools/cloud-launch/cloud_launch.py'
        source = Path(os.environ.get('TEST_LAUNCH_SOURCE', str(default)))
        tree = ast.parse(source.read_text(encoding='utf-8'))
        function, = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'evidence']
        with tempfile.TemporaryDirectory() as directory:
            held = Path(directory)/'held.json'
            held.write_bytes(b'fixture')
            target = {'cluster_id': 'current', 'namespace_uid': 'ns', 'secret_uid': 'secret', 'ca_uid': 'ca',
                      'held_bundle': str(held), 'held_bundle_sha256': hashlib.sha256(b'fixture').hexdigest()}
            sql = {'status': 'PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION', 'data_mode': 'fresh_test',
                   'cluster_id': 'current', 'new_cluster_bound': True}
            handoff = {'data_mode': 'fresh_test', 'target_database_host': 'db', 'target_redis_host': 'redis'}
            values = {'handoff': handoff, 'sql': sql, 'target': target, str(held): {
                'items': [{'kind': 'ConfigMap', 'data': {'SEOKPAN_DATABASE_EXPECTED_HOST': 'db'}}]},
                'schema': self.wrapper}
            def need(ok, reason):
                if not ok:
                    raise RuntimeError(reason)
            loader = types.SimpleNamespace(exec_module=lambda module: None)
            contract = types.SimpleNamespace(accepted_handoff=lambda value: None)
            imports = types.SimpleNamespace(util=types.SimpleNamespace(
                spec_from_file_location=lambda *args: types.SimpleNamespace(loader=loader),
                module_from_spec=lambda value: contract))
            namespace = {'read': lambda path: values[path], 'need': need, 'Stop': RuntimeError,
                'Path': Path, 'hashlib': hashlib, 'HERE': Path(directory), 'importlib': imports, 'ref': bool}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
            c = {'cluster_id': 'current', 'namespace_uid': 'ns', 'database_host': 'db', 'redis_host': 'redis',
                 'protected_uids': {'secret/backend-db-runtime': 'secret', 'configmap/backend-database-ca': 'ca'}}
            evidence = {'final_data_handoff': 'handoff', 'purpose_sql_receipt': 'sql', 'purpose_sql_target': 'target',
                'migration_current': '20260902_0002', 'migration_current_ref': 'schema',
                'captured_transition_accepted': True, 'captured_transition_ref': 'schema', 'pool_candidate_accepted': True}
            with self.assertRaisesRegex(RuntimeError, '^CURRENT_FRESH_EMPTY_SCHEMA_OBSERVATION_REQUIRED$'):
                namespace['evidence'](c, evidence, 'single')
            values['schema'] = M.observation(self.wrapper, self.binding)
            namespace['evidence'](c, evidence, 'single')


if __name__ == '__main__':
    unittest.main()
