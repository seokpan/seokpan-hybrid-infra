"""새 데이터 분기의 합성 검증만 수행한다. DB/클러스터/SSH 호출은 없다."""
import asyncio
import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

C = load('fresh_contract', 'tools/cloud-readiness/accepted_sql_contract.py')
Q = load('fresh_sql_probe', 'tools/cloud-readiness/accepted_sql_probe.py')
P = load('fresh_prestart', 'tools/cloud-prestart/prestart.py')
R = load('fresh_runtime', 'tools/cloud-prestart/runtime_probe.py')
L = load('fresh_launch', 'tools/cloud-launch/cloud_launch.py')
N = load('fresh_new_cluster', 'tools/cloud-readiness/post_import_new_cluster.py')

def handoff():
    return dict(data_mode='fresh_test', accepted_by='tjung03', fresh_test_target_accepted=True,
                legacy_targets_excluded=True, target_database_host='db.example.test',
                target_database_name='stone_game', target_redis_host='cache.example.test',
                fresh_target_evidence_ref='synthetic-target', purpose_accounts_evidence_ref='synthetic-accounts',
                schema_prepared_evidence_ref='synthetic-schema', reported_schema_revision='20260902_0002')

def runtime_payload():
    p = handoff()
    p.update(database_host=p['target_database_host'], redis_host=p['target_redis_host'],
             schema_url='mysql+asyncmy://db_admin:synthetic@db.example.test:3306/stone_game',
             database_ca_sha256=R.hashlib.sha256(b'synthetic-ca').hexdigest(),
             redis_ca_sha256=R.hashlib.sha256(b'synthetic-ca').hexdigest(), redis_auth='synthetic')
    return p

class Cursor:
    def __init__(self, nonempty=False, version='20260902_0002', tls=True):
        self.queries = []
        self.nonempty, self.version, self.tls = nonempty, version, tls
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def execute(self, query): self.queries.append(query)
    async def fetchone(self):
        q = self.queries[-1]
        if 'Ssl_cipher' in q: return ('Ssl_cipher', 'TLS_TEST' if self.tls else '')
        if 'CURRENT_USER' in q: return ('identity_svc@fixture', 80, 0)
        return (0,)
    async def fetchall(self):
        q = self.queries[-1]
        if 'alembic_version' in q: return [(self.version,)]
        if 'SHOW GRANTS' in q: return [('GRANT SELECT ON stone_game.* TO identity_svc MAX_USER_CONNECTIONS 20',)]
        if 'LIMIT 1' in q: return [(1,)] if self.nonempty else []
        return []

class Connection:
    def __init__(self, cursor): self.cur, self.closed = cursor, False
    def cursor(self): return self.cur
    def close(self): self.closed = True

class Redis:
    async def scan(self, **kwargs): return 0, []
    async def aclose(self): pass

class FreshData(unittest.TestCase):
    def test_fresh_contract_without_import_or_stop(self):
        self.assertTrue(C.accepted_handoff(handoff()))
    def test_missing_account_schema_isolation_evidence_rejected(self):
        for k in ('fresh_target_evidence_ref', 'schema_prepared_evidence_ref', 'purpose_accounts_evidence_ref',
                  'legacy_targets_excluded', 'reported_schema_revision', 'accepted_by'):
            h = handoff(); h.pop(k)
            with self.subTest(key=k), self.assertRaises(C.Stop): C.accepted_handoff(h)
    def test_fresh_cannot_forge_legacy_success(self):
        for k in ('source_writer_hold_accepted', 'final_import_compare_and_schema_handoff_accepted'):
            h = handoff(); h[k] = True
            with self.subTest(key=k), self.assertRaises(C.Stop): C.accepted_handoff(h)
    def test_legacy_guards_remain(self):
        for mode in ('migrated', None):
            h = handoff(); h.pop('data_mode')
            if mode: h['data_mode'] = mode
            with self.assertRaises(C.Stop): C.accepted_handoff(h)
        with self.assertRaises(Q.RequirementError): Q.validate_handoff({'database': 'stone_game'})
        with self.assertRaises(R.Stop): R.data_admission(runtime_payload() | {'data_mode': 'migrated'})
    def test_unknown_modes_rejected(self):
        with self.assertRaises(C.Stop): C.accepted_handoff(handoff() | {'data_mode': 'typo'})
        with self.assertRaises(Q.RequirementError): Q.validate_handoff({'data_mode': 'typo'})
        with self.assertRaises(R.Stop): R.data_admission({'data_mode': 'typo'})
    def bundle(self):
        data = {'SEOKPAN_DATABASE_EXPECTED_HOST': 'db.example.test', 'SEOKPAN_DATABASE_EXPECTED_NAME': 'stone_game',
                'SEOKPAN_DATABASE_EXPECTED_PORT': '3306', 'SEOKPAN_REDIS_EXPECTED_HOST': 'cache.example.test'}
        secret = {'data': {k: base64.b64encode(('mysql+asyncmy://' + u + ':synthetic@db.example.test:3306/stone_game').encode()).decode()
                  for k, u in [('SEOKPAN_IDENTITY_DATABASE_URL', 'identity_svc'), ('SEOKPAN_GAME_DATABASE_URL', 'game_svc')]}}
        return {'items': [{'kind': 'ConfigMap', 'data': data}]}, secret
    def test_protected_payload_retains_targets_without_legacy_booleans(self):
        b, s = self.bundle(); p = C.runtime_payload(b, s, 'synthetic-ca', handoff())
        self.assertEqual(p['data_mode'], 'fresh_test')
        self.assertNotIn('source_writer_hold_accepted', p)
        self.assertEqual(Q.validate_handoff(p), 'fresh_test')
    def test_fresh_host_or_redis_mismatch_rejected(self):
        b, s = self.bundle()
        for k in ('target_database_host', 'target_redis_host'):
            h = handoff(); h[k] = 'wrong.example.test'
            with self.subTest(key=k), self.assertRaises(C.Stop): C.runtime_payload(b, s, 'synthetic-ca', h)
    def test_invalid_payload_stops_before_sql_connect(self):
        calls = []
        async def connect(**kwargs): calls.append(kwargs); raise AssertionError('NO_SQL')
        with self.assertRaises(Q.RequirementError): asyncio.run(Q.assess({'data_mode': 'fresh_test'}, object(), connect))
        self.assertEqual(calls, [])
    def test_current_non_68_limit_keeps_tls_account_reads(self):
        b, s = self.bundle(); p = C.runtime_payload(b, s, 'synthetic-ca', handoff())
        cur = Cursor(); conn = Connection(cur)
        async def connect(**kwargs): return conn
        x = asyncio.run(Q.check_account(p['accounts'][0], p, object(), connect))
        self.assertEqual(x['status'], 'PASS_PURPOSE_ACCOUNT_READ_ONLY')
        self.assertTrue(x['tls']); self.assertEqual(x['max_connections'], 80)
        self.assertTrue(any('CURRENT_USER' in q for q in cur.queries))
        self.assertTrue(all(not q.startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER')) for q in cur.queries))
    def test_tls_failure_still_rejects(self):
        b, s = self.bundle(); p = C.runtime_payload(b, s, 'synthetic-ca', handoff())
        cur = Cursor(tls=False)
        async def connect(**kwargs): return Connection(cur)
        x = asyncio.run(Q.check_account(p['accounts'][0], p, object(), connect))
        self.assertEqual(x['status'], 'STOP_PURPOSE_ACCOUNT_READ_ONLY')
    def test_complete_fresh_sql_assess_uses_both_purpose_accounts(self):
        b, s = self.bundle(); p = C.runtime_payload(b, s, 'synthetic-ca', handoff())
        calls = []
        class PurposeCursor(Cursor):
            async def fetchone(self):
                if 'CURRENT_USER' in self.queries[-1]: return (self.user+'@fixture', 80, 0)
                return await super().fetchone()
        async def connect(**kwargs):
            calls.append(kwargs['user']); cur = PurposeCursor(); cur.user = kwargs['user']; return Connection(cur)
        x = asyncio.run(Q.assess(p, object(), connect))
        self.assertEqual(x['status'], 'PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION')
        self.assertEqual(calls, ['identity_svc', 'game_svc']); self.assertEqual(x['database_writes'], 0)
    def test_prestart_schema_payload_does_not_manufacture_legacy_flags(self):
        f = P.Flow.__new__(P.Flow); f.handoff = handoff(); f.verify_pod = lambda *a: None
        f.L = SimpleNamespace(oc_client=lambda *a: None)
        f.c = {'personal_kubeconfig': 'synthetic', 'oc': 'synthetic'}; f.runner = None
        f.schema = {'database_url': runtime_payload()['schema_url']}
        seen = []
        def call(*args, payload=None):
            if args[0] == 'exec':
                seen.append(json.loads(payload))
                return json.dumps({'status': 'PASS_SCHEMA_AND_CAPTURED_ADMISSION_NOT_BUSINESS'}).encode()
            name = args[2]
            return json.dumps({'metadata': {'uid': name}, 'data': {'SEOKPAN_REDIS_AUTH_TOKEN': base64.b64encode(b'synthetic').decode(), 'ca.crt': 'synthetic'}}).encode()
        f.call = call
        c = {'database_host': 'db.example.test', 'redis_host': 'cache.example.test', 'protected_uids': {
            'secret/backend-redis-runtime': 'backend-redis-runtime', 'configmap/backend-database-ca': 'backend-database-ca',
            'configmap/backend-redis-ca': 'backend-redis-ca'}}
        f.schema_mode({'pod_name': 'synthetic'}, c)
        self.assertEqual(seen[0]['data_mode'], 'fresh_test')
        self.assertNotIn('all_old_writers_stopped', seen[0]); self.assertNotIn('final_data_accepted', seen[0])
    def runtime(self, nonempty=False, version='20260902_0002'):
        cur = Cursor(nonempty=nonempty, version=version); conn = Connection(cur)
        async def connect(**kwargs): return conn
        with patch('builtins.open', return_value=io.BytesIO(b'synthetic-ca')), patch.object(R.ssl, 'create_default_context'):
            # 두 CA 파일은 독립된 읽기 스트림으로 대체한다.
            with patch('builtins.open', side_effect=lambda *a, **k: io.BytesIO(b'synthetic-ca')):
                result = asyncio.run(R.assess(runtime_payload(), connect, lambda **k: Redis()))
        return result, cur
    def test_fresh_runtime_checks_all_tables_empty_and_current_schema(self):
        x, cur = self.runtime()
        self.assertTrue(x['fresh_database_empty_verified']); self.assertEqual(x['schema_revision'], '20260902_0002')
        self.assertEqual(sum('LIMIT 1' in q for q in cur.queries), 7)
        self.assertEqual(x['database_writes'], 0)
    def test_nonempty_target_is_not_deleted_or_admitted(self):
        with self.assertRaisesRegex(R.Stop, 'NOT_EMPTY'): self.runtime(nonempty=True)
    def test_missing_or_wrong_schema_is_not_upgraded_or_stamped(self):
        with self.assertRaisesRegex(R.Stop, 'SCHEMA_DIFFERENCE'): self.runtime(version='wrong')
    def test_pool_uses_actual_limit_and_preserves_initial_budget(self):
        sql = {'status': 'PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION', 'data_mode': 'fresh_test',
               'accounts': [{'account': u, 'status': 'PASS_PURPOSE_ACCOUNT_READ_ONLY', 'tls': True,
                             'max_connections': 80, 'account_limit_covers_candidate_14': True}
                            for u in ('identity_svc', 'game_svc')]}
        self.assertEqual(P.pool_choice(sql)['accepted_database_limit'], 80)
        for change in ('low', 'tls', 'account'):
            bad = copy.deepcopy(sql)
            if change == 'low':
                for r in bad['accounts']: r['max_connections'] = 42
            elif change == 'tls': bad['accounts'][0]['tls'] = False
            else: bad['accounts'][0]['account_limit_covers_candidate_14'] = False
            with self.subTest(change=change), self.assertRaises(P.Stop): P.pool_choice(bad)
    def launch_fixture(self, root):
        c = {'cluster_id': 'newcluster', 'namespace_uid': 'newns', 'database_host': 'db.example.test',
             'redis_host': 'cache.example.test', 'protected_uids': {'secret/backend-db-runtime': 's', 'configmap/backend-database-ca': 'ca'}}
        held = root/'held.json'; L.write(held, {'items': [{'kind': 'ConfigMap', 'data': {'SEOKPAN_DATABASE_EXPECTED_HOST': c['database_host']}}]})
        sql = {'status': 'PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION', 'data_mode': 'fresh_test',
               'cluster_id': 'newcluster', 'new_cluster_bound': True}
        target = {'cluster_id': 'newcluster', 'namespace_uid': 'newns', 'secret_uid': 's', 'ca_uid': 'ca',
                  'held_bundle': str(held), 'held_bundle_sha256': L.hashlib.sha256(held.read_bytes()).hexdigest()}
        observed = {'status': 'PASS_SCHEMA_AND_CAPTURED_ADMISSION_NOT_BUSINESS', 'data_mode': 'fresh_test',
                    'fresh_database_empty_verified': True, 'schema_revision': '20260902_0002', 'database_writes': 0, 'redis_writes': 0}
        e = {'migration_current': '20260902_0002', 'migration_current_ref': 'synthetic-current',
             'captured_transition_accepted': True, 'pool_candidate_accepted': True}
        for k, v in [('final_data_handoff', handoff()), ('purpose_sql_receipt', sql), ('purpose_sql_target', target),
                     ('captured_transition_ref', observed)]:
            p = root/(k+'.json'); L.write(p, v); e[k] = p.as_posix()
        return c, e
    def test_launch_fresh_path_requires_real_sql_and_observation_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, e = self.launch_fixture(Path(tmp)); L.evidence(c, e, 'single')
    def test_actual_linux_schema_receipt_reference_is_supported_and_bounded(self):
        self.assertTrue(L.ref('/home/jth/run/schema-mode.json'))
        self.assertFalse(L.ref('/home/jth/password=value'))
        self.assertFalse(L.ref('/home/jth/' + 'x'*256))
    def test_launch_rejects_old_sql_status_or_unverified_empty_state(self):
        for field in ('purpose_sql_receipt', 'captured_transition_ref'):
            with tempfile.TemporaryDirectory() as tmp:
                c, e = self.launch_fixture(Path(tmp)); path = Path(e[field]); x = L.read(path)
                if field == 'purpose_sql_receipt': x['data_mode'] = 'migrated'
                else: x['fresh_database_empty_verified'] = False
                path.write_text(json.dumps(x)); path.chmod(0o600)
                with self.subTest(field=field), self.assertRaises(L.Stop): L.evidence(c, e, 'single')
    def test_complete_legacy_evidence_still_uses_original_import_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, e = self.launch_fixture(Path(tmp))
            p = Path(e['final_data_handoff']); p.write_text(json.dumps({'accepted_by': 'tjung03',
                'final_import_compare_and_schema_handoff_accepted': True, 'source_writer_hold_accepted': True,
                'reported_schema_revision': '20260902_0002', 'final_import_evidence_ref': 'synthetic-import',
                'source_writer_hold_evidence_ref': 'synthetic-stop'})); p.chmod(0o600)
            p = Path(e['purpose_sql_receipt']); x = L.read(p)
            x.update(status='PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION', data_mode='migrated')
            p.write_text(json.dumps(x)); p.chmod(0o600); L.evidence(c, e, 'single')
            h = L.read(e['final_data_handoff']); h['source_writer_hold_accepted'] = False
            p = Path(e['final_data_handoff']); p.write_text(json.dumps(h)); p.chmod(0o600)
            with self.assertRaisesRegex(L.Stop, 'FINAL_DATA'): L.evidence(c, e, 'single')
    def test_fresh_empty_observation_success_is_reused_without_new_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = P.Flow.__new__(P.Flow); f.work = Path(tmp); f.binding = {'synthetic': 'fresh'}; f.resume_readonly = False
            first = f.step('schema-mode', lambda: {'status': 'PASS_SYNTHETIC_FRESH_EMPTY'})
            again = f.step('schema-mode', lambda: self.fail('COMPLETED_READ_REPEATED'))
            self.assertEqual(first, again)
    def test_changed_contract_and_probe_hash_pins_match(self):
        self.assertTrue(N.load_contract().accepted_handoff(handoff()))
        self.assertEqual(N.PROBE_SHA, N.hashlib.sha256((ROOT/'tools/cloud-readiness/accepted_sql_probe.py').read_bytes()).hexdigest())

if __name__ == '__main__': unittest.main()
