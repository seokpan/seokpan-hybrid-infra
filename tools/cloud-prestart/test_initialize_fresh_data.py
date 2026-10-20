"""Only new empty-DB and current-master mutation boundaries; no live calls."""
import asyncio
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

s=importlib.util.spec_from_file_location('initializer',Path(__file__).with_name('initialize_fresh_data.py'))
M=importlib.util.module_from_spec(s);s.loader.exec_module(M)


class Initialization(unittest.TestCase):
    def scenario(self,existing_db=False,existing_user=False,migration_fail=False):
        n={};exec(M.REMOTE.split('def migrate(p):')[0],n)
        state={'db':existing_db,'users':['existing'] if existing_user else [],'migrated':False,'queries':[]}
        host='test.rds.amazonaws.com';account_host='192.168.64.0/255.255.240.0'
        payload={'data_mode':'fresh_test','expected_revision':'20260902_0002','target_database':'stone_game',
            'ca_sha256':hashlib.sha256(b'ca').hexdigest(),'database_host':host,
            'schema_url':'mysql+asyncmy://db_admin:protected-value@'+host+':3306/stone_game',
            'purpose_urls':{u:'mysql+asyncmy://'+u+':protected-value@'+host+':3306/stone_game' for u in ('identity_svc','game_svc')},
            'master':{'username':'master','password':'protected-value'},'account_host':account_host,'approval_ref':'USER_TEST_ONLY'}
        class Cursor:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def execute(self,sql,args=None):
                state['queries'].append(sql);self.rows=[]
                if 'Ssl_cipher' in sql:self.rows=[('Ssl_cipher','cipher')]
                elif 'SELECT User,Host,plugin' in sql:self.rows=[(u,account_host,'mysql_native_password','ANY') for u in state['users']]
                elif 'SELECT User,Host' in sql:self.rows=[(u,account_host) for u in state['users']]
                elif 'information_schema.SCHEMATA' in sql:self.rows=[('stone_game',)] if state['db'] else []
                elif sql.startswith('CREATE DATABASE'):state['db']=True
                elif 'version_num' in sql:self.rows=[('20260902_0002',)] if state['migrated'] else []
                elif 'information_schema.TABLES' in sql:self.rows=[(x,) for x in (*n['TABLES'],'alembic_version')]
                elif sql.startswith('CREATE USER'):state['users'].append(args[0])
                elif sql.startswith(('GRANT ','SELECT 1 FROM')):pass
                else:raise AssertionError(sql)
            async def fetchall(self):return self.rows
            async def fetchone(self):return self.rows[0]
        class Connection:
            def cursor(self):return Cursor()
            def close(self):pass
        async def connect(**kwargs):
            self.assertTrue(kwargs['ssl']);return Connection()
        def migrate(p):
            state['migrated']=not migration_fail
            return types.SimpleNamespace(returncode=1 if migration_fail else 0,stdout=b'',stderr=b'')
        with patch('builtins.open',lambda *a,**k:io.BytesIO(b'ca')),patch.object(n['ssl'],'create_default_context',lambda **k:types.SimpleNamespace()):
            result=asyncio.run(n['execute'](payload,connect,migrate))
        self.assertNotIn('protected-value',json.dumps(result))
        self.assertFalse(any(q.startswith(('DROP ','ALTER ','UPDATE ','DELETE ','INSERT ')) for q in state['queries']))
        return result,state

    def test_empty_database_and_absent_accounts_only(self):
        result,state=self.scenario()
        self.assertEqual(result['status'],'PASS_FRESH_SCHEMA_AND_PURPOSE_ACCOUNTS_PREPARED')
        self.assertTrue(result['database_created'])
        self.assertTrue(result['schema_upgrade_executed'])
        self.assertEqual(result['accounts_created'],['identity_svc','game_svc'])
        self.assertEqual(result['grant_statements_executed'],8)
        self.assertEqual(result['data_rows_written'],0)

    def test_existing_account_is_not_absence_or_password_reset(self):
        result,state=self.scenario(existing_user=True)
        self.assertIn('EXISTING_PURPOSE_ACCOUNT',result['reason'])
        self.assertFalse(result['database_create_attempted'])
        self.assertEqual(state['users'],['existing'])

    def test_existing_database_is_not_upgraded_or_deleted(self):
        result,state=self.scenario(existing_db=True)
        self.assertEqual(result['reason'],'DATABASE_NOW_EXISTS_RECONCILE_NO_DDL')
        self.assertFalse(result['schema_upgrade_attempted'])

    def test_failed_migration_reports_partial_creation_without_account_retry(self):
        result,state=self.scenario(migration_fail=True)
        self.assertTrue(result['database_created'])
        self.assertTrue(result['schema_upgrade_attempted'])
        self.assertFalse(result['schema_upgrade_executed'])
        self.assertEqual(result['accounts_created'],[])
        self.assertFalse(result['automatic_retry'])

    def secret_fixture(self):
        arn='arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:rds-current'
        db={'DBInstanceStatus':'available','Endpoint':{'Address':'test.rds.amazonaws.com','Port':3306},
            'MasterUserSecret':{'SecretArn':arn},'MasterUsername':'master'}
        meta={'ARN':arn,'VersionIdsToStages':{'current':['AWSCURRENT']}}
        value={'ARN':arn,'VersionId':'current','VersionStages':['AWSCURRENT'],
            'SecretString':json.dumps({'username':'master','password':'protected-value','host':'test.rds.amazonaws.com'})}
        return db,meta,value

    def test_current_master_bound_to_exact_rds_and_version(self):
        db,meta,value=self.secret_fixture()
        self.assertEqual(M.master_value(db,meta,value,'test.rds.amazonaws.com','123456789012')['username'],'master')

    def test_pending_rotation_or_mismatched_secret_rejected(self):
        for change in ('pending','version','arn','host'):
            db,meta,value=self.secret_fixture()
            if change=='pending':meta['VersionIdsToStages']['pending']=['AWSPENDING']
            if change=='version':value['VersionId']='old'
            if change=='arn':value['ARN']='another'
            if change=='host':db['Endpoint']['Address']='another.rds.amazonaws.com'
            with self.assertRaises(RuntimeError):M.master_value(db,meta,value,'test.rds.amazonaws.com','123456789012')


if __name__=='__main__':unittest.main()
