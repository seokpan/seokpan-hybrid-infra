"""Only new read-only setup inspection paths; no database or cluster access."""
import asyncio
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('inspection',Path(__file__).with_name('inspect_fresh_data.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)


class DriverError(Exception):pass


class Inspection(unittest.TestCase):
    def run_case(self,exists=True,populated=False,denied=False,mismatch=False):
        n={}
        exec(M.REMOTE.split('async def main():')[0],n)
        host='cloud.test.rds.amazonaws.com'
        payload={'database_host':host,'redis_host':'cloud.test.cache.amazonaws.com',
                 'schema_url':'mysql+asyncmy://'+('other' if mismatch else 'db_admin')+':not-a-real-password@'+host+':3306/stone_game',
                 'purpose_urls':{u:'mysql+asyncmy://'+u+':not-a-real-password@'+host+':3306/stone_game' for u in ('identity_svc','game_svc')},
                 'redis_auth':'not-a-real-password','database_ca_sha256':hashlib.sha256(b'ca').hexdigest(),
                 'redis_ca_sha256':hashlib.sha256(b'ca').hexdigest()}
        seen=[]
        class Cursor:
            def __init__(self,user):self.user=user;self.rows=[]
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def execute(self,sql,args=None):
                seen.append(sql)
                if sql.startswith('SET '):self.rows=[]
                elif 'Ssl_cipher' in sql:self.rows=[('Ssl_cipher','cipher')]
                elif 'max_connections' in sql:self.rows=[(68,0)]
                elif 'SCHEMATA' in sql:self.rows=[('stone_game',)] if exists else []
                elif 'information_schema.TABLES' in sql:self.rows=[(x,) for x in (*n['TABLES'],'alembic_version')]
                elif 'version_num' in sql:self.rows=[('20260902_0002',)]
                elif sql.startswith('SELECT 1 FROM'):self.rows=[(1,)] if populated else []
                elif 'CURRENT_USER()' in sql and sql.startswith('SELECT'):self.rows=[(self.user+'@matched',)]
                elif sql.startswith('SHOW GRANTS'):self.rows=[('GRANT SELECT ON stone_game.* TO user REQUIRE SSL',)]
                else:raise AssertionError(sql)
            async def fetchall(self):return self.rows
        class Connection:
            def __init__(self,user):self.user=user
            def cursor(self):return Cursor(self.user)
            def close(self):pass
        async def connect(**kw):
            if denied:raise DriverError(1045,'not-a-real-password')
            if not exists and kw['db']:raise DriverError(1049,'not-a-real-password')
            self.assertTrue(kw['ssl'])
            return Connection(kw['user'])
        class Redis:
            def __init__(self,**kw):
                assert kw['ssl'] and kw['ssl_check_hostname']
            async def ping(self):return True
            async def aclose(self):pass
        with patch('builtins.open',lambda *a,**k:io.BytesIO(b'ca')),patch.object(n['ssl'],'create_default_context',lambda **k:types.SimpleNamespace()):
            result=asyncio.run(n['inspect'](payload,connect,Redis))
        self.assertEqual(result['database_writes'],0)
        self.assertEqual(result['schema_changes'],0)
        self.assertNotIn('not-a-real-password',json.dumps(result))
        self.assertTrue(all(q.startswith(('SELECT ','SHOW ','SET SESSION TRANSACTION READ ONLY')) for q in seen))
        return result

    def test_missing_database_does_not_create_or_fake_schema(self):
        r=self.run_case(exists=False)
        self.assertFalse(r['schema']['database_exists'])
        self.assertFalse(r['schema']['current_revision_matches'])
        self.assertTrue(all(x['error_code']==1049 for x in r['accounts']))

    def test_current_schema_accounts_tls_redis_collected(self):
        r=self.run_case()
        self.assertTrue(r['schema']['current_revision_matches'])
        self.assertTrue(r['schema']['all_expected_tables_present'])
        self.assertEqual(r['redis']['status'],'TLS_AUTH_PING_OK')
        self.assertTrue(all(x['status']=='LOGIN_AND_TLS_OK' and not x['full_grant_contract_accepted'] for x in r['accounts']))

    def test_existing_data_is_reported_not_deleted(self):
        self.assertEqual(len(self.run_case(populated=True)['schema']['populated_application_tables']),7)

    def test_auth_error_is_sanitized(self):
        r=self.run_case(denied=True)
        self.assertEqual(r['schema']['error_code'],1045)
        self.assertEqual(r['schema']['status'],'NOT_ACCEPTED')

    def test_wrong_schema_account_is_rejected_before_connect(self):
        r=self.run_case(mismatch=True)
        self.assertEqual(r['schema']['reason'],'EXACT_PROTECTED_DATABASE_TARGET_REQUIRED')


if __name__=='__main__':unittest.main()
