"""Only new handoff/bridge/materialization boundaries; no existing project suite."""
import base64
import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ACTUAL_UID=os.getuid() if hasattr(os,'getuid') else 0

def load(name):
    spec=importlib.util.spec_from_file_location('closing_'+name,HERE/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

DB=load('db_bridge');M=load('materialize_inputs');P=load('finish_prestart');POD=load('pod_db_observer');BIND=load('bind_runtime')
if os.name=='posix':
    H=load('readonly_handoff');T=load('metadata_transport');D=load('d_generator')
else:
    with patch.dict(sys.modules,{'fcntl':types.SimpleNamespace(),'pwd':types.SimpleNamespace()}):H=load('readonly_handoff')
    with patch.dict(sys.modules,{'fcntl':types.SimpleNamespace(flock=lambda *a:None,LOCK_EX=1)}):T=load('metadata_transport')
    with patch.dict(sys.modules,{'pwd':types.SimpleNamespace()}):D=load('d_generator')

class Boundaries(unittest.TestCase):
    def test_metadata_never_transfers_sources_or_credentials(self):
        p=DB.packet({'paths':{'credential_file':'/private/a.cnf','ca_file':'/public/a.pem'}},'metadata')
        self.assertEqual(p['files'],{});self.assertIsNone(p['config'])
        self.assertNotIn('password',json.dumps(p))

    def test_existing_ssh_is_batch_only_and_preserves_known_hosts(self):
        seen=[]
        def runner(argv,**kw):
            seen.append(argv);return types.SimpleNamespace(returncode=0,stdout=b'{"status":"COLLECTED_DB_HOST_METADATA_NO_SQL"}')
        DB.invoke({'ssh_prefix':['ssh'],'peer':'root@192.0.2.3','remote_python':'python3'},
                  {'action':'metadata','timeout':10},runner)
        self.assertIn('BatchMode=yes',seen[0]);self.assertIn('StrictHostKeyChecking=yes',seen[0])
        self.assertNotIn('StrictHostKeyChecking=no',seen[0]);self.assertNotIn('-A',seen[0])

    def test_wrong_db_target_stops_before_source_loading(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'db_probe.py').write_text('pass');(p/'common.py').write_text('pass')
            s={'paths':{},'hpa_source':t,'source_sha256':{n:hashlib.sha256(b'pass').hexdigest() for n in ['common.py','db_probe.py']},'database_host':'rds.correct'}
            with self.assertRaisesRegex(RuntimeError,'SAME_EXPECTED_DATABASE'):DB.packet(s,'probe',{'binding':{'x':1},'database_host':'source.wrong'})

    def test_hpa_code_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'db_probe.py').write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'ADOPTED_DB_OBSERVER'):
                DB.packet({'paths':{},'hpa_source':t,'source_sha256':{'db_probe.py':'0'*64}},'probe',{'binding':{'x':1}})

    def test_runtime_extract_drops_old_uids_and_root_metadata(self):
        rows=[]
        for (kind,name),keys in M.EXPECTED.items():
            data={k:'a' for k in keys}
            if name=='backend-db-runtime':
                data={k:base64.b64encode(('mysql+asyncmy://'+user+':private@rds.correct:3306/stone_game').encode()).decode()
                    for k,user in [('SEOKPAN_IDENTITY_DATABASE_URL','identity_svc'),('SEOKPAN_GAME_DATABASE_URL','game_svc')]}
            rows.append({'kind':kind,'metadata':{'namespace':'seokpan-cloud','name':name,'uid':'OLD','ownerReferences':[{}]},'data':data,'type':'Opaque'})
        out=M.runtime_objects({'snapshots':rows},'rds.correct')
        self.assertEqual(len(out['items']),4)
        self.assertTrue(all(set(o['metadata'])=={'name','namespace'} for o in out['items']))
        with self.assertRaisesRegex(RuntimeError,'EXACT_PRESERVED_RDS_TARGET'):
            M.runtime_objects({'snapshots':rows},'source.wrong')

    def test_atomic_feed_grants_read_before_rename(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'latest.json';calls=[]
            with patch.object(H,'acl',lambda f,r,directory=False:calls.append((f.exists(),p.exists(),r))):
                H.publish_file(p,b'new','reader')
            self.assertEqual(calls,[(True,False,'reader')]);self.assertEqual(p.read_bytes(),b'new')

    def test_immutable_published_config_cannot_change(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'config.json';p.write_bytes(b'old')
            with patch.object(H.os,'getuid',create=True,return_value=p.stat().st_uid):
                with self.assertRaisesRegex(RuntimeError,'EXISTING_PUBLICATION_DIFFERS'):
                    H.publish_file(p,b'new','reader',True)

    def test_secret_extension_never_published(self):
        with self.assertRaisesRegex(RuntimeError,'EXACT_APPROVED_HPA_FIELDS'):
            H.public_contract({'password':'sensitive'})

    def test_manual_candidate_edit_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'candidate.json';P.persist_candidate(p,{'schema':1,'value':None})
            p.write_text('{"schema":1,"value":"operator bound"}')
            with self.assertRaisesRegex(RuntimeError,'EXISTING_BOUND_OR_DIFFERENT'):
                P.persist_candidate(p,{'schema':1,'value':'other'})

    def test_duplicate_identical_input_allowed_conflicting_inputs_not_selected(self):
        with tempfile.TemporaryDirectory() as t:
            a=Path(t)/'a.json';b=Path(t)/'b.json';a.write_text('{"v":1}');b.write_text('{"v":1}')
            self.assertIsNotNone(P.one_existing([a,b]));b.write_text('{"v":2}')
            self.assertIsNone(P.one_existing([a,b]))

    def test_remote_loader_compiles_without_running(self):
        compile(DB.REMOTE,'remote-existing-db-host','exec')

    def test_guard_original_timestamp_and_target_are_preserved(self):
        now=dt.datetime.now(dt.timezone.utc)
        row={'config_sha256':'a','binding':{'uid':'new'},'status':'PASS_HPA_GUARD','utc':now.isoformat()}
        self.assertEqual(H.guard_row(row,'a',{'uid':'new'},now.timestamp()),row)
        with self.assertRaisesRegex(RuntimeError,'STALE'):H.guard_row(row,'a',{'uid':'new'},now.timestamp()+46)
        with self.assertRaisesRegex(RuntimeError,'TARGET_CHANGED'):H.guard_row(row,'b',{'uid':'new'},now.timestamp())

    def test_guard_stop_status_never_becomes_pass(self):
        row={'config_sha256':'a','binding':{},'status':'STOP_HPA_GUARD','utc':dt.datetime.now(dt.timezone.utc).isoformat()}
        with self.assertRaisesRegex(RuntimeError,'SOURCE_GUARD_STOPPED'):H.guard_row(row,'a',{},0)

    def test_pod_observer_wrong_or_source_credential_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'EXACT_EXISTING_DB_OBSERVER'):
            POD.target({'database_host':'rds.correct'},{'schema':1,'database_url':'mysql+asyncmy://db_admin:p@source.wrong:3306/stone_game'})
        compile(POD.PROGRAM,'pod-read-only-observer','exec')

    def test_hpa_ceiling_accounts_for_surge_terminating_and_each_account(self):
        q={'processes':1,'pool_size':2,'overflow':0,'surge_allowance':1,'terminating_allowance':2,
           'global_limit':68,'reserved':10,'node_backend_capacity':13,
           'accounts':{'identity':{'max_user_connections':10,'global_max_user_connections':0},
                       'game':{'max_user_connections':0,'global_max_user_connections':0}}}
        self.assertEqual(BIND.hpa_ceiling(q,2),2)
        q['accounts']['identity']['max_user_connections']=8
        with self.assertRaisesRegex(RuntimeError,'NO_SAFE_HPA_RANGE'):BIND.hpa_ceiling(q,2)

    def test_metadata_transport_path_argument_is_not_a_file_selector(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'settings.json';path.write_text(json.dumps({'run':t,'out':t}))
            with patch.object(T.os,'getuid',create=True,return_value=ACTUAL_UID):
                with self.assertRaisesRegex(RuntimeError,'EXACT_STAGE_REQUIRED'):
                    T.route('b-stage-request-v1 ../../secrets',path)

    def test_metadata_transport_delegates_to_original_argv_without_shell(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);script=root/'original.py';script.write_bytes(b'original exact allowlist')
            settings=root/'settings.json';settings.write_text(json.dumps({'run':t,'out':t,'original_dispatch_sha256':T.sha(script)}))
            (root/'original-dispatch.json').write_text(json.dumps({'argv':['python3','-I',str(script)]}))
            calls=[]
            with patch.object(T,'HERE',root),patch.object(T.os,'getuid',create=True,return_value=ACTUAL_UID),patch.object(T.os,'execv',lambda binary,argv:calls.append((binary,argv))):
                T.route('cat /private/secret',settings)
            self.assertEqual(calls,[('python3',['python3','-I',str(script)])])

    def test_pod_observer_stdin_only_and_uid_change_is_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            schema=Path(t)/'schema.json';schema.write_text(json.dumps({'schema':1,'database_url':'mysql+asyncmy://db_admin:private-password@rds.correct:3306/stone_game'}))
            schema.chmod(0o600)
            c={'binding':{'cluster_id':'new0123456789','namespace_uid':'ns','application_uid':'app','normalized_source_sha256':'source'},
                'schema_credentials_file':str(schema),'database_host':'rds.correct','oc':'oc','personal_kubeconfig':'/private/k',
                'api':'https://api.new.test:6443','backend_image':'image@sha256:approved'}
            changed=[False];calls=[]
            def runner(argv,**kw):
                calls.append((argv,kw.get('input')))
                args=argv[3:]
                if args[:2]==['config','view']:v={'clusters':[{'cluster':{'server':c['api']}}]}
                elif args==['whoami']:return types.SimpleNamespace(returncode=0,stdout=b'tjung03\n')
                elif args[:2]==['get','namespace']:v={'metadata':{'uid':'ns'}}
                elif args[:2]==['get','application']:v={'metadata':{'uid':'app'},'spec':{'source':{}}}
                elif args[:2]==['get','pods']:v={'items':[{'metadata':{'uid':'pod','name':'backend-one'},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]},'spec':{'containers':[{'image':c['backend_image']}]}}]}
                elif args[0]=='exec':v={'status':'PASS_CURRENT_DB_SAMPLE','binding':c['binding']}
                elif args[:2]==['get','pod']:v={'metadata':{'uid':'replacement' if changed[0] else 'pod'}}
                else:raise AssertionError(args)
                return types.SimpleNamespace(returncode=0,stdout=json.dumps(v).encode())
            common=types.SimpleNamespace(digest=lambda x:'source',normalized=lambda x:x)
            with patch.object(POD,'load_common',lambda c:common):
                self.assertEqual(POD.sample(c,runner)['collector_pod_uid'],'pod')
                changed[0]=True
                with self.assertRaisesRegex(RuntimeError,'OBSERVER_POD_REPLACED'):POD.sample(c,runner)
            self.assertFalse(any('private-password' in ' '.join(argv) for argv,_ in calls))
            self.assertTrue(any(payload and b'private-password' in payload for _,payload in calls))

    def test_task_key_install_keeps_other_keys_and_can_reuse(self):
        with tempfile.TemporaryDirectory() as t:
            home=Path(t);(home/'.ssh').mkdir();script=home/'original.py';script.write_text('exact old guard')
            public='ssh-ed25519 AAAATEST task';keys=home/'.ssh/authorized_keys'
            other='ssh-ed25519 OTHER unchanged\n'
            keys.write_text(other+'restrict,command="/usr/bin/python3.9 -I '+script.as_posix()+'" '+public+'\n')
            settings=home/'settings.json';settings.write_text(json.dumps({'public_key':public,'python':'/usr/bin/python3.9',
                'original_dispatch_sha256':T.sha(script)}))
            def posix_replace(source,dest):
                # Windows cannot replace an open locked file. Model the Linux
                # operation here; this test does not assert native Linux flock/ACL.
                Path(dest).write_bytes(Path(source).read_bytes());Path(source).unlink()
            with patch.object(T,'HERE',home),patch.object(T.Path,'home',return_value=home),patch.object(T.os,'getuid',create=True,return_value=ACTUAL_UID),patch.object(T.os,'O_NOFOLLOW',create=True,new=0),patch.object(T.os,'replace',posix_replace):
                self.assertEqual(T.install(settings)['status'],'PASS_TASK_METADATA_TRANSPORT_INSTALLED')
                self.assertEqual(T.install(settings)['status'],'REUSED_TASK_METADATA_TRANSPORT')
            self.assertTrue(keys.read_text().startswith(other));self.assertEqual(keys.read_text().count(public),1)

    def test_unrecognized_original_dispatch_cannot_change_task_key(self):
        with tempfile.TemporaryDirectory() as t:
            home=Path(t);(home/'.ssh').mkdir();script=home/'original.py';script.write_text('unknown guard')
            public='ssh-ed25519 AAAATEST task';keys=home/'.ssh/authorized_keys'
            raw='restrict,command="/usr/bin/python3.9 -I '+script.as_posix()+'" '+public+'\n';keys.write_text(raw)
            settings=home/'settings.json';settings.write_text(json.dumps({'public_key':public,'python':'/usr/bin/python3.9',
                'original_dispatch_sha256':'0'*64}))
            with patch.object(T,'HERE',home),patch.object(T.Path,'home',return_value=home),patch.object(T.os,'getuid',create=True,return_value=ACTUAL_UID),patch.object(T.os,'O_NOFOLLOW',create=True,new=0):
                with self.assertRaisesRegex(RuntimeError,'EXACT_ORIGINAL_RESTRICTED'):T.install(settings)
            self.assertEqual(keys.read_text(),raw)

    def test_d_return_rejects_receipt_from_another_run(self):
        with tempfile.TemporaryDirectory() as t:
            work=Path(t);(work/'receipt.json').write_text(json.dumps({'run_id':'other','binding':{},'control_sha256':'a',
                'in_flight_at_end':0,'status':'COMPLETED_HPA_LOAD'}))
            with self.assertRaisesRegex(RuntimeError,'SAME_STOPPED_GENERATOR'):
                D.return_results({'run_id':'current','binding':{},'control_sha256':'a'},work,work/'return','jth')

    def test_d_return_never_copies_arbitrary_files(self):
        with tempfile.TemporaryDirectory() as t:
            work=Path(t);plan={'run_id':'current','binding':{},'control_sha256':'a'}
            (work/'receipt.json').write_text(json.dumps({**plan,'in_flight_at_end':0,'status':'ABORTED_HPA_LOAD'}))
            (work/'SHA256SUMS.json').write_text(json.dumps({'../private-credential':'hash'}))
            with self.assertRaisesRegex(RuntimeError,'EXACT_GENERATOR_RESULT_FILES'):
                D.return_results(plan,work,work/'return','jth')

    def test_dispatch_accepts_only_two_explicit_byte_variants(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'dispatch.py';p.write_bytes(b'import os\n');plain=T.sha(p)
            p.write_bytes(b'\nimport os\n');leading=T.sha(p)
            c={'original_dispatch_sha256':plain,'original_dispatch_sha256_variants':[leading]}
            self.assertIn(T.sha(p),T.accepted_dispatch_hashes(c))
            p.write_bytes(b'import subprocess\n');self.assertNotIn(T.sha(p),T.accepted_dispatch_hashes(c))

    def test_dispatch_hash_allowlist_is_bounded_and_not_wildcard(self):
        for c in [{'original_dispatch_sha256':'*'},
                  {'original_dispatch_sha256':'a'*64,'original_dispatch_sha256_variants':['b'*64,'c'*64]}]:
            with self.assertRaisesRegex(RuntimeError,'EXACT_REVIEWED_DISPATCH_HASHES'):T.accepted_dispatch_hashes(c)

if __name__=='__main__':unittest.main(verbosity=2)
