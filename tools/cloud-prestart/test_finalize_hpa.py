import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

for name in ('fcntl', 'pwd'):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = types.ModuleType(name)

spec = importlib.util.spec_from_file_location('F', Path(__file__).with_name('finalize_hpa.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


class Boundaries(unittest.TestCase):
    def fixture(self):
        account = '847835841591'
        arn = 'arn:aws:secretsmanager:ap-northeast-2:'+account+':secret:test-only'
        db = {'DBInstanceStatus': 'available', 'Endpoint': {'Address': 'test.local', 'Port': 3306},
              'MasterUsername': 'test_admin', 'MasterUserSecret': {'SecretArn': arn}}
        meta = {'ARN': arn, 'VersionIdsToStages': {'v1': ['AWSCURRENT']}}
        value = {'ARN': arn, 'VersionId': 'v1', 'VersionStages': ['AWSCURRENT'],
                 'SecretString': json.dumps({'username': 'test_admin', 'password': 'fake-only'})}
        return db, meta, value, 'test.local', account

    def test_current_master_bound(self):
        self.assertEqual(F.master_value(*self.fixture())['username'], 'test_admin')

    def test_pending_rotation_rejected(self):
        args = self.fixture()
        args[1]['VersionIdsToStages']['v2'] = ['AWSPENDING']
        with self.assertRaisesRegex(RuntimeError, 'MASTER_ROTATION_PENDING'):
            F.master_value(*args)

    def test_other_secret_rejected(self):
        args = self.fixture()
        args[2]['ARN'] = 'other'
        with self.assertRaisesRegex(RuntimeError, 'EXACT_MASTER_SECRET_REQUIRED'):
            F.master_value(*args)

    def test_other_host_rejected(self):
        args = self.fixture()
        args[2]['SecretString'] = json.dumps({'username': 'test_admin', 'password': 'fake', 'host': 'other'})
        with self.assertRaisesRegex(RuntimeError, 'BOUND_MASTER_CREDENTIAL_REQUIRED'):
            F.master_value(*args)

    def test_capacity_conservatively_counts_init_and_other_namespaces(self):
        nodes = [{'metadata': {'name': 'n'}, 'status': {'allocatable': {'cpu': '1', 'memory': '2Gi', 'pods': '20'}}}]
        pods = [{'metadata': {'namespace': 'other'}, 'spec': {'nodeName': 'n',
                 'containers': [{'resources': {'requests': {'cpu': '200m', 'memory': '256Mi'}}}],
                 'initContainers': [{'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}}}]},
                 'status': {'phase': 'Running'}}]
        slots, _ = F.node_capacity(nodes, pods, ['n'])
        self.assertEqual(slots, 6)

    def test_capacity_excludes_only_own_backend(self):
        nodes = [{'metadata': {'name': 'n'}, 'status': {'allocatable': {'cpu': '1', 'memory': '1Gi', 'pods': '20'}}}]
        def pod(ns):
            return {'metadata': {'namespace': ns, 'labels': {'app.kubernetes.io/name': 'backend'}},
                    'spec': {'nodeName': 'n', 'containers': [{'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}}}]},
                    'status': {'phase': 'Running'}}
        self.assertEqual(F.node_capacity(nodes, [pod('seokpan-cloud'), pod('other')], ['n'])[0], 3)

    def remote(self, present, allow):
        import ast
        import hashlib
        import io
        from unittest.mock import patch
        calls = []
        state = {'present': present}
        class Cursor:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def execute(self, sql, args=None):
                self.sql = sql
                calls.append((sql, args))
                if sql.startswith('GRANT '): state['present'] = True
            async def fetchone(self):
                if self.sql.startswith('SELECT CURRENT_USER'): return ('db_admin@192.168.64.0/255.255.240.0',)
                return ('Ssl_cipher', 'TLS_FAKE')
            async def fetchall(self):
                return [('GRANT '+('PROCESS' if state['present'] else 'USAGE')+' ON *.* TO db_admin',)]
        class Connection:
            def cursor(self): return Cursor()
            def close(self): pass
        async def connect(**kwargs): return Connection()
        fake = types.SimpleNamespace(connect=connect)
        context = types.SimpleNamespace(minimum_version=None)
        tree = ast.parse(F.GRANT_PROGRAM)
        tree.body = [node for node in tree.body if not isinstance(node, ast.Try)]
        env = {}
        with patch.dict(sys.modules, {'asyncmy': fake}), patch('builtins.open', lambda *a: io.BytesIO(b'fake-ca')), \
             patch('ssl.create_default_context', return_value=context):
            exec(compile(tree, '<remote>', 'exec'), env)
            payload = {'ca_file': '/fake', 'ca_sha256': hashlib.sha256(b'fake-ca').hexdigest(),
                       'schema_url': 'mysql+asyncmy://db_admin:fake@test.local:3306/stone_game',
                       'host': 'test.local', 'allow_grant': allow,
                       'master': {'username': 'test_admin', 'password': 'fake'}}
            try: return asyncio.run(env['apply'](payload)), calls
            except RuntimeError as error: return error, calls

    def test_existing_process_does_not_grant_again(self):
        result, calls = self.remote(True, True)
        self.assertFalse(result['grant_added'])
        self.assertFalse(any(sql.startswith('GRANT ') for sql, _ in calls))

    def test_only_matched_db_admin_gets_process(self):
        result, calls = self.remote(False, True)
        self.assertTrue(result['grant_added'])
        writes = [(sql, args) for sql, args in calls if sql.startswith('GRANT ')]
        self.assertEqual(writes, [('GRANT PROCESS ON *.* TO %s@%s', ('db_admin', '192.168.64.0/255.255.240.0'))])

    def test_previous_attempt_cannot_repeat_missing_grant(self):
        result, calls = self.remote(False, False)
        self.assertIsInstance(result, RuntimeError)
        self.assertFalse(any(sql.startswith('GRANT ') for sql, _ in calls))

    def test_python39_syntax(self):
        import ast
        ast.parse(Path(F.__file__).read_text(), feature_version=(3, 9))
        ast.parse(F.GRANT_PROGRAM, feature_version=(3, 9))


if __name__ == '__main__':
    unittest.main()
