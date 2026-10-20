import copy
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('ca_observer',Path(__file__).with_name('pod_db_observer.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

class DatabaseCaTests(unittest.TestCase):
    def setUp(self):
        self.pod={'spec':{'containers':[{'name':'backend','volumeMounts':[
            {'name':'db-ca','mountPath':'/etc/seokpan/database-ca','readOnly':True}]}],
            'volumes':[{'name':'db-ca','configMap':{'name':'backend-database-ca',
                        'items':[{'key':'ca.crt','path':'bundle/ca.pem'}]}}]}}

    def test_uses_live_mount_and_item_mapping(self):
        self.assertEqual(M.database_ca_path(self.pod),'/etc/seokpan/database-ca/bundle/ca.pem')

    def test_supports_default_configmap_key_projection(self):
        self.pod['spec']['volumes'][0]['configMap'].pop('items')
        self.assertEqual(M.database_ca_path(self.pod),'/etc/seokpan/database-ca/ca.crt')

    def test_rejects_missing_or_writable_or_subpath_mount(self):
        for change in ('missing','writable','subpath'):
            pod=copy.deepcopy(self.pod);mount=pod['spec']['containers'][0]['volumeMounts'][0]
            if change=='missing':pod['spec']['volumes'][0]['configMap']['name']='other'
            if change=='writable':mount['readOnly']=False
            if change=='subpath':mount['subPath']='ca.crt'
            with self.assertRaises(RuntimeError):M.database_ca_path(pod)

    def test_rejects_path_escape_and_ambiguous_projection(self):
        for path in ('../ca.crt','/unrelated/ca.crt',''):
            pod=copy.deepcopy(self.pod)
            pod['spec']['volumes'][0]['configMap']['items'][0]['path']=path
            with self.assertRaises(RuntimeError):M.database_ca_path(pod)
        self.pod['spec']['containers'][0]['volumeMounts'].append(copy.deepcopy(self.pod['spec']['containers'][0]['volumeMounts'][0]))
        with self.assertRaises(RuntimeError):M.database_ca_path(self.pod)

class VisibilityScopeTests(unittest.TestCase):
    def remote(self,scope=None,process=False):
        class Cursor:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def execute(self,q):self.query=q
            async def fetchone(self):
                if 'Ssl_cipher' in self.query:return ('Ssl_cipher','TLS')
                if 'max_connections' in self.query:return (68,0)
                return ('Threads_connected','17')
            async def fetchall(self):
                if 'SHOW GRANTS' in self.query:return [('GRANT '+('PROCESS' if process else 'USAGE')+' ON *.* TO x',)]
                return [('identity_svc',2),('game_svc',4)]
        class Conn:
            def cursor(self):return Cursor()
            def close(self):pass
        async def connect(**kw):return Conn()
        with tempfile.TemporaryDirectory() as t:
            ca=Path(t)/'ca.crt';ca.write_bytes(b'fixture-ca')
            packet={'database_url':'mysql+asyncmy://db_admin:p@rds:3306/stone_game','binding':{},
                    'ca_file':str(ca),'ca_sha256':hashlib.sha256(ca.read_bytes()).hexdigest()}
            if scope is not None:packet['observation_scope']=scope
            out=io.StringIO();exitcode=0
            with patch.dict(sys.modules,{'asyncmy':types.SimpleNamespace(connect=connect)}),patch('ssl.create_default_context',return_value=types.SimpleNamespace()),patch.object(sys,'stdin',io.StringIO(json.dumps(packet))),contextlib.redirect_stdout(out):
                try:exec(compile(M.PROGRAM,'synthetic-db-program','exec'),{})
                except SystemExit as e:exitcode=e.code
            return exitcode,json.loads(out.getvalue())

    def test_default_full_scope_rejects_unproven_process(self):
        code,r=self.remote();self.assertEqual(code,2)
        self.assertEqual(r['reason'],'GLOBAL_PROCESS_VISIBILITY_REQUIRED')

    def test_explicit_total_scope_never_fabricates_account_zeros(self):
        code,r=self.remote('TOTAL_ONLY');self.assertEqual(code,0)
        self.assertEqual(r['status'],'PASS_CURRENT_DB_TOTAL_SAMPLE')
        self.assertEqual(r['connections_total'],17);self.assertIsNone(r['per_account'])
        self.assertFalse(r['account_visibility_accepted'])

    def test_process_proof_preserves_full_account_sample(self):
        code,r=self.remote(process=True);self.assertEqual(code,0)
        self.assertEqual(r['status'],'PASS_CURRENT_DB_SAMPLE')
        self.assertEqual(r['per_account'],{'identity_svc':2,'game_svc':4})
        self.assertTrue(r['account_visibility_accepted'])

if __name__=='__main__':unittest.main()
