"""New synthetic boundaries only; no cluster, credentials or old suites."""
import asyncio
import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, file):
    s = importlib.util.spec_from_file_location(name, HERE/file)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


P = load('prestart_tests_subject', 'prestart.py')
R = load('new_runtime_subject', 'runtime_probe.py')


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)

    def idle(self):
        wanted = P.pod_spec('data-admin-abcdef', 'abcdef', 'registry.example/backend@sha256:'+'a'*64)
        live = copy.deepcopy(wanted)
        live['metadata']['uid'] = '11111111-2222-3333-4444-555555555555'
        live['status'] = {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]}
        return wanted, live

    def flow(self):
        f = P.Flow.__new__(P.Flow)
        f.work = self.root
        f.binding = {'cluster_id': 'newcluster12345', 'input_sha256': 'f'*64}
        f.cluster = 'newcluster12345'
        f.resume_readonly = False
        f.runner = None
        return f

    def test_default_missing_inputs_never_loads_runtime(self):
        c = {k: None for k in P.FIELDS}
        c['schema'] = 1
        config = self.root/'inputs.json'
        P.write(config, c)
        with patch.object(P, 'Flow', side_effect=AssertionError('NO_RUNTIME')), patch('sys.argv', ['entry', '--config', str(config)]), patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(P.main(), 2)
        self.assertEqual(json.loads(out.getvalue())['api_calls'], 0)

    def test_idle_native_pull_pod_and_uid(self):
        wanted, live = self.idle()
        self.assertEqual(P.validate_pod(live, wanted), live['metadata']['uid'])
        self.assertNotIn('imagePullSecrets', wanted['spec'])
        self.assertFalse(wanted['spec']['automountServiceAccountToken'])

    def test_pod_rejects_old_pull_and_secret_mount_and_extra_hooks(self):
        wanted, live = self.idle()
        changes = [lambda x: x['spec'].update(imagePullSecrets=[{'name': 'seokpan-cloud-ecr-pull'}]),
                   lambda x: x['spec']['volumes'].append({'name': 'private', 'secret': {'secretName': 'secret'}}),
                   lambda x: x['spec']['containers'][0].update(env=[{'name': 'PASSWORD', 'value': 'synthetic'}]),
                   lambda x: x['spec'].update(hostNetwork=True),
                   lambda x: x['spec']['containers'][0]['securityContext'].update(privileged=True)]
        for change in changes:
            bad = copy.deepcopy(live)
            change(bad)
            with self.subTest(change=change), self.assertRaises(P.Stop):
                P.validate_pod(bad, wanted)

    def test_pod_rejects_uid_reuse_or_image_change(self):
        wanted, live = self.idle()
        with self.assertRaises(P.Stop):
            P.validate_pod(live, wanted, 'another-uid')
        live['spec']['containers'][0]['image'] += '-changed'
        with self.assertRaises(P.Stop):
            P.validate_pod(live, wanted)

    def test_unknown_pod_create_is_read_reconciled_not_created_again(self):
        f = self.flow()
        wanted, live = self.idle()
        f.wanted = lambda c: wanted
        P.write(f.work/'pod-create.attempted.json', {'binding': f.binding, 'desired_sha256': P.digest(P.canon(wanted))})
        held = f.work/'held-bundle.json'
        P.write(held, {'items': []})
        f.c = {'oc': 'oc', 'personal_kubeconfig': 'synthetic'}
        calls = []
        def call(*args, **kw):
            calls.append(args)
            self.assertEqual(args[:2], ('get', 'pod'))
            return P.canon(live)
        f.call = call
        c = {'namespace_uid': 'new-ns', 'protected_uids': {'secret/backend-db-runtime': 'new-secret', 'configmap/backend-database-ca': 'new-ca'}}
        self.assertEqual(f.data_pod(c)['target']['pod_uid'], live['metadata']['uid'])
        self.assertEqual(len(calls), 1)

    def test_unknown_missing_pod_create_does_not_retry(self):
        f = self.flow()
        wanted, _ = self.idle()
        f.wanted = lambda c: wanted
        P.write(f.work/'pod-create.attempted.json', {'binding': f.binding})
        calls = []
        f.call = lambda *a, **k: calls.append(a) or b''
        with self.assertRaisesRegex(P.Stop, 'NO_RECREATE'):
            f.data_pod({})
        self.assertEqual(len(calls), 1)

    def test_success_receipt_skips_probe_on_resume(self):
        f = self.flow()
        calls = []
        self.assertEqual(f.step('probe', lambda: calls.append(1) or {'status': 'PASS_SYNTHETIC'})['status'], 'PASS_SYNTHETIC')
        self.assertEqual(f.step('probe', lambda: self.fail('REPEATED'))['status'], 'PASS_SYNTHETIC')
        self.assertEqual(calls, [1])

    def test_interrupted_readonly_requires_explicit_resume(self):
        f = self.flow()
        P.write(f.work/'probe.started.json', {'binding': f.binding})
        with self.assertRaises(P.Stop):
            f.step('probe', lambda: self.fail('UNAUTHORIZED_RETRY'), readonly=True)
        f.resume_readonly = True
        self.assertEqual(f.step('probe', lambda: {'status': 'PASS_RECOVERED'}, readonly=True)['status'], 'PASS_RECOVERED')

    def test_changed_cached_run_binding_is_rejected(self):
        f = self.flow()
        P.write(f.work/'probe.json', {'binding': {'cluster_id': 'old'}, 'result': {'status': 'PASS_OLD'}})
        with self.assertRaises(P.Stop):
            f.step('probe', lambda: self.fail('MUST_NOT_RUN'))

    def test_pool_uses_actual_two_account_limits(self):
        sql = {'status': 'PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION', 'accounts': [
            {'account': u, 'status': 'PASS_PURPOSE_ACCOUNT_READ_ONLY', 'max_connections': 68,
             'account_limit_covers_candidate_14': True} for u in ('identity_svc', 'game_svc')]}
        self.assertEqual(P.pool_choice(sql)['candidate_total'], 38)
        sql['accounts'][1]['account_limit_covers_candidate_14'] = False
        with self.assertRaises(P.Stop):
            P.pool_choice(sql)

    def test_captured_requires_absent_runtime_and_no_active_or_inconsistent_games(self):
        self.assertEqual(R.mode_admission(0, 0, 0)['mode'], 'captured')
        for values in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            with self.subTest(values=values), self.assertRaises(R.Stop):
                R.mode_admission(*values)

    def test_scan_count_is_not_hard_limit_and_tail_page_is_checked(self):
        class Redis:
            def __init__(self): self.calls = 0
            async def scan(self, **kw):
                self.calls += 1
                return (7, []) if self.calls == 1 else (0, [b'private-key-not-printed'])
        r = Redis()
        self.assertEqual(asyncio.run(R.bounded_room_scan(r)), 1)
        self.assertEqual(r.calls, 2)

    def test_scan_overflow_does_not_become_empty_runtime_pass(self):
        class Redis:
            async def scan(self, **kw): return 3, []
        with self.assertRaises(R.Stop):
            asyncio.run(R.bounded_room_scan(Redis(), max_pages=1))

    def test_schema_reader_rejects_wrong_host_before_socket(self):
        async def connect(**kw): self.fail('SOCKET_MUST_NOT_OPEN')
        with self.assertRaisesRegex(R.Stop, 'TARGET'):
            asyncio.run(R.assess({'schema_url': 'mysql+asyncmy://db_admin:synthetic@wrong:3306/stone_game',
                                  'database_host': 'right'}, connect, None))

    def test_execution_evidence_is_generated_from_actual_results_and_refs(self):
        f = self.flow()
        f.c = {'final_data_handoff': '/private/final.json'}
        result = f.evidence({}, {'schema_revision': '20260902_0002',
                                'mode_admission': {'status': 'PASS_CAPTURED_EMPTY_RUNTIME_ADMISSION'}},
                            {'status': 'PASS_INITIAL_POOL_SELECTION_NOT_RUNTIME_TEST'})
        e = P.read(result['path'])
        self.assertTrue(e['captured_transition_accepted'])
        self.assertTrue(e['pool_candidate_accepted'])
        self.assertTrue(e['purpose_sql_receipt'].endswith('purpose-sql-result.json'))

    def test_partial_local_package_is_not_overwritten(self):
        f = self.flow()
        (f.work/'package').mkdir()
        with self.assertRaisesRegex(P.Stop, 'PARTIAL_LOCAL_PACKAGE'):
            f.package({})

    def test_uncertain_source_patch_never_repatched(self):
        f = self.flow()
        receipt = f.work/'single-source.json'
        P.write(receipt.with_name(receipt.name+'.STARTED_NO_AUTOMATIC_RETRY.json'),
                {'cluster_id': f.cluster, 'app_uid': 'app-uid', 'stage': 'single', 'mutation': 'APPLICATION_SOURCE_ONLY'})
        f.c = {'personal_kubeconfig': 'synthetic', 'oc': 'oc'}
        f.runner = None
        calls = []
        def read_only(*args):
            calls.append(args)
            return P.canon({'metadata': {'uid': 'app-uid'}, 'spec': {'source': {'different': True}}})
        f.L = types.SimpleNamespace(APP='app', ARGO='argo', package=lambda *a: ({}, {}, {'expected': True}, {}, {}),
            oc_client=lambda *a: read_only, current_app=lambda a: a['metadata'], normalize_source=lambda x: x,
            stage_app=lambda *a: self.fail('REPATCH_FORBIDDEN'))
        with self.assertRaisesRegex(P.Stop, 'NO_REPATCH'):
            f.stage_or_reconcile('single', {}, receipt, {})
        self.assertEqual(calls[0][0], 'get')

    def test_cleanup_uses_exact_uid_and_lost_response_only_reads(self):
        f = self.flow()
        wanted, live = self.idle()
        f.wanted = lambda c: wanted
        target = {'pod_name': wanted['metadata']['name'], 'pod_uid': live['metadata']['uid']}
        P.write(f.work/'new-pod.json', {'result': {'target': target}})
        P.write(f.work/'new-inputs.json', {'result': {'inputs': {}}})
        calls = []
        def call(*args, payload=None):
            calls.append(args)
            if args[0] == 'delete':
                self.assertEqual(json.loads(payload)['preconditions']['uid'], target['pod_uid'])
                raise P.Stop('SYNTHETIC_RESPONSE_LOST')
            return P.canon(live)
        f.call = call
        with self.assertRaises(P.Stop):
            f.cleanup_pod()
        f.call = lambda *a, **kw: calls.append(a) or b''
        self.assertEqual(f.cleanup_pod()['status'], 'PASS_OWN_IDLE_POD_REMOVAL_CONFIRMED')
        self.assertEqual(sum(x[0] == 'delete' for x in calls), 1)

    def test_schema_actual_query_path_stops_mismatch_without_redis(self):
        queries = []
        class Cursor:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, q): queries.append(q)
            async def fetchone(self): return ('Ssl_cipher', 'TLS_AES_256_GCM_SHA384')
            async def fetchall(self): return [('older_schema',)]
        class Connection:
            def cursor(self): return Cursor()
            def close(self): queries.append('CLOSED')
        async def connect(**kw): return Connection()
        payload = {'schema_url': 'mysql+asyncmy://db_admin:synthetic@db.example:3306/stone_game',
                   'database_host': 'db.example', 'database_ca_sha256': P.digest(b'public-ca'),
                   'redis_ca_sha256': P.digest(b'public-ca')}
        with patch('builtins.open', side_effect=lambda *a, **kw: io.BytesIO(b'public-ca')), \
             patch.object(R.ssl, 'create_default_context', return_value=types.SimpleNamespace()):
            with self.assertRaisesRegex(R.Stop, 'SCHEMA_DIFFERENCE'):
                asyncio.run(R.assess(payload, connect, lambda **kw: self.fail('REDIS_MUST_NOT_OPEN')))
        self.assertIn('SELECT version_num FROM stone_game.alembic_version', queries)
        self.assertEqual(queries[0], 'SET SESSION TRANSACTION READ ONLY')
        self.assertEqual(queries[-1], 'CLOSED')

    def test_schema_and_empty_mode_positive_path_only_reads(self):
        queries, sockets = [], []
        class Cursor:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def execute(self, q): queries.append(q)
            async def fetchone(self):
                return ('Ssl_cipher', 'TLS_AES') if queries[-1].startswith('SHOW') else (0,)
            async def fetchall(self): return [('20260902_0002',)]
        class Connection:
            def cursor(self): return Cursor()
            def close(self): sockets.append('SQL_CLOSED')
        async def connect(**kw):
            self.assertEqual(kw['user'], 'db_admin')
            sockets.append('SQL')
            return Connection()
        class Redis:
            async def scan(self, **kw): return 0, []
            async def aclose(self): sockets.append('REDIS_CLOSED')
        def factory(**kw):
            self.assertTrue(kw['ssl_check_hostname'])
            self.assertEqual(kw['ssl_cert_reqs'], 'required')
            sockets.append('REDIS')
            return Redis()
        payload = {'schema_url': 'mysql+asyncmy://db_admin:synthetic@db.example:3306/stone_game',
                   'database_host': 'db.example', 'database_ca_sha256': P.digest(b'public-ca'),
                   'redis_ca_sha256': P.digest(b'public-ca'), 'redis_host': 'redis.example', 'redis_auth': 'synthetic'}
        with patch('builtins.open', side_effect=lambda *a, **kw: io.BytesIO(b'public-ca')), \
             patch.object(R.ssl, 'create_default_context', return_value=types.SimpleNamespace()):
            result = asyncio.run(R.assess(payload, connect, factory))
        self.assertEqual(result['status'], 'PASS_SCHEMA_AND_CAPTURED_ADMISSION_NOT_BUSINESS')
        self.assertTrue(all(q.startswith(('SET SESSION TRANSACTION READ ONLY', 'SELECT ', 'SHOW ')) for q in queries))
        self.assertEqual(sockets, ['SQL', 'SQL_CLOSED', 'REDIS', 'REDIS_CLOSED'])

    def test_before_sync_connects_steps_once_and_builds_real_reference_files(self):
        f = self.flow()
        f.c = {'final_data_handoff': '/private/final.json', 'personal_kubeconfig': 'synthetic', 'oc': 'oc'}
        calls = []
        f.L = types.SimpleNamespace(oc_client=lambda *a: None)
        c = {'new_identity': True}
        f.supply_inputs = lambda: calls.append('supply') or {'status': 'PASS_SUPPLY'}
        f.bind_inputs = lambda: calls.append('bind') or {'status': 'PASS_BOUND', 'inputs': c}
        f.package = lambda c: calls.append('package') or {'status': 'PASS_PACKAGE'}
        f.data_pod = lambda c: calls.append('pod') or {'status': 'PASS_POD', 'target': {'pod_uid': 'new-pod'}}
        sql = {'status': 'PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION', 'accounts': [
            {'account': u, 'status': 'PASS_PURPOSE_ACCOUNT_READ_ONLY', 'max_connections': 68,
             'account_limit_covers_candidate_14': True} for u in ('identity_svc', 'game_svc')]}
        f.sql = lambda t, c: calls.append('sql') or sql
        f.schema_mode = lambda t, c: calls.append('schema') or {
            'status': 'PASS_SCHEMA_AND_CAPTURED_ADMISSION_NOT_BUSINESS', 'schema_revision': '20260902_0002',
            'mode_admission': {'status': 'PASS_CAPTURED_EMPTY_RUNTIME_ADMISSION'}}
        self.assertEqual(f.before_sync(), c)
        self.assertEqual(P.read(f.work/'purpose-sql-result.json'), sql)
        e = P.read(f.work/'execution-evidence.json')
        self.assertEqual(e['purpose_sql_target'], str(f.work/'sql-target.json'))
        f.before_sync()
        self.assertEqual(calls, ['supply', 'bind', 'package', 'pod', 'sql', 'schema'])

    def test_new_platform_guard_stops_before_any_network(self):
        lifecycle, platform = self.root/'lifecycle.json', self.root/'platform.json'
        P.write(lifecycle, {'current_cluster_id': 'oldcluster', 'source_cluster_id': 'oldcluster',
                            'create': {'last_platform_handoff': str(platform)}})
        P.write(platform, {'cluster_id': 'oldcluster', 'state': 'ready'})
        with patch.object(P, 'helpers', return_value=(None, None)):
            with self.assertRaisesRegex(P.Stop, 'NO_RECREATION'):
                P.Flow({'lifecycle_config': str(lifecycle)}, self.root,
                       runner=lambda *a, **kw: self.fail('NETWORK_MUST_NOT_START'))

    def supply_fixture(self):
        f = self.flow()
        kube = self.root/'kubeconfig'
        P.write(kube, {'synthetic': True})
        b64 = lambda s: base64.b64encode(s.encode()).decode()
        rows = [
            {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'backend-db-runtime', 'namespace': P.NS},
             'type': 'Opaque', 'data': {k: b64('mysql+asyncmy://'+u+':synthetic@db.example:3306/stone_game') for k, u in
                (('SEOKPAN_IDENTITY_DATABASE_URL', 'identity_svc'), ('SEOKPAN_GAME_DATABASE_URL', 'game_svc'))}},
            {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'backend-redis-runtime', 'namespace': P.NS},
             'type': 'Opaque', 'data': {'SEOKPAN_REDIS_AUTH_TOKEN': b64('synthetic')}},
            *[{'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': n, 'namespace': P.NS},
               'data': {'ca.crt': 'public-ca'}} for n in ('backend-database-ca', 'backend-redis-ca')]]
        inputs = self.root/'runtime.json'
        P.write(inputs, {'items': rows})
        f.c = {'runtime_inputs_file': str(inputs), 'personal_kubeconfig': str(kube)}
        f.platform = {'api_url': 'https://api.new.example:6443'}
        f.launch = {'database_host': 'db.example'}
        f.L = types.SimpleNamespace(APP='app', ARGO='argo', current_app=lambda app: {})
        live = {o['metadata']['name']: copy.deepcopy(o) for o in rows}
        for i, o in enumerate(live.values()):
            o['metadata'].update(uid='new-uid-'+str(i), resourceVersion='1')
        calls = []
        def call(*args, payload=None):
            calls.append(args)
            if args[0] == 'config': return P.canon({'clusters': [{'cluster': {'server': f.platform['api_url']}}]})
            if args[0] == 'whoami': return b'tjung03'
            if args[:2] == ('get', 'namespace'): return P.canon({'metadata': {'uid': 'new-ns'}})
            if args[:2] == ('get', 'deployment'): return P.canon({'items': []})
            if args[:2] == ('get', 'application'): return P.canon({})
            if args[0] == 'get': return P.canon(live[args[2]])
            if args[0] == 'patch':
                patch = json.loads(payload)
                self.assertEqual(patch[0]['path'], '/metadata/uid')
                live[args[2]]['data'] = patch[-1]['value']
                return b'patched'
            self.fail('UNEXPECTED_CALL')
        f.call = call
        return f, live, rows, calls

    def test_unchanged_four_protected_inputs_are_not_resupplied(self):
        f, _, _, calls = self.supply_fixture()
        result = f.supply_inputs()
        self.assertEqual(result['data_updates'], 0)
        self.assertFalse(any(c[0] == 'patch' for c in calls))

    def test_only_changed_runtime_data_is_conditionally_patched(self):
        f, live, _, calls = self.supply_fixture()
        live['backend-redis-runtime']['data'] = {'SEOKPAN_REDIS_AUTH_TOKEN': 'b2xk'}
        result = f.supply_inputs()
        self.assertEqual(result['data_updates'], 1)
        self.assertEqual([c[2] for c in calls if c[0] == 'patch'], ['backend-redis-runtime'])
        f.supply_inputs()
        self.assertEqual(sum(c[0] == 'patch' for c in calls), 1)

    def test_protected_input_unknown_patch_does_not_write_again(self):
        f, live, rows, calls = self.supply_fixture()
        name = 'backend-redis-runtime'
        desired = next(x for x in rows if x['metadata']['name'] == name)
        P.write(f.work/('supply-'+name+'.attempted.json'), {'binding': f.binding, 'namespace_uid': 'new-ns',
                'uid': live[name]['metadata']['uid'], 'desired_sha256': P.digest(P.canon(desired['data']))})
        live[name]['data'] = {'SEOKPAN_REDIS_AUTH_TOKEN': 'b2xk'}
        with self.assertRaisesRegex(P.Stop, 'NO_SECOND_WRITE'):
            f.supply_inputs()
        self.assertFalse(any(c[0] == 'patch' for c in calls))

    def test_pull_secret_cannot_enter_runtime_supply(self):
        f, _, _, calls = self.supply_fixture()
        bundle = P.read(f.c['runtime_inputs_file'])
        bundle['items'][1]['metadata']['name'] = 'seokpan-cloud-ecr-pull'
        Path(f.c['runtime_inputs_file']).unlink()
        P.write(f.c['runtime_inputs_file'], bundle)
        with self.assertRaisesRegex(P.Stop, 'EXACT_FOUR'):
            f.supply_inputs()
        self.assertFalse(any(c[0] == 'patch' for c in calls))


if __name__ == '__main__':
    unittest.main()
