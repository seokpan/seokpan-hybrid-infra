import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io
import subprocess
import sys
from timeline import Ledger
from rolling_control import desired_source, operation_patch, templates
from db_observation import parse, observed_summary, budget_report, account_budget
from compare_runs import compare
import rolling_control
from timeline import sha
import stage_clock
from types import SimpleNamespace


class Clock:
    def __init__(self): self.n = 0;self.boot = 'one'
    def __call__(self):
        self.n += 1_000_000_000
        return {'host': 'reference', 'boot_id': self.boot, 'monotonic_ns': self.n, 'utc': '2026-10-19T00:00:00Z'}


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.path = Path(self.temp.name)/'timeline.jsonl';self.clock = Clock()
    def tearDown(self): self.temp.cleanup()
    def test_isolated_cli_imports_siblings_without_cloud_access(self):
        for name in ('stage_clock.py', 'rolling_control.py', 'db_observation.py', 'compare_runs.py'):
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).parent/name), '--help'], capture_output=True)
            self.assertEqual(r.returncode, 0, name)
    def test_first_start_preserved_resume(self):
        a = Ledger(self.path, {'stage': 'single'}, self.clock)
        first = a.mark('s0', {})
        a.mark('failure', {})
        b = Ledger(self.path, {'stage': 'single'}, self.clock)
        self.assertEqual(b.mark('s0', {}), first)
        for s in ('s1', 's2', 's3', 's4'): b.mark(s, {})
        self.assertGreater(b.summary()['seconds_first_execute_to_external_receipt'], 4)
    def test_clock_cross_boot_rejected(self):
        Ledger(self.path, {}, self.clock).mark('s0', {});self.clock.boot = 'two'
        with self.assertRaisesRegex(RuntimeError, 'REBOOT'): Ledger(self.path, {}, self.clock)
    def test_cross_stage_rejected(self):
        Ledger(self.path, {'stage': 'single'}, self.clock).mark('s0', {})
        with self.assertRaisesRegex(RuntimeError, 'BINDING'): Ledger(self.path, {'stage': 'target'}, self.clock)
    def test_chain_tampering_rejected(self):
        Ledger(self.path, {}, self.clock).mark('s0', {})
        self.path.write_text(self.path.read_text().replace('"s0"', '"s1"'))
        with self.assertRaisesRegex(RuntimeError, 'CHAIN'): Ledger(self.path, {}, self.clock)
    def test_order_required(self):
        with self.assertRaisesRegex(RuntimeError, 'ORDER'): Ledger(self.path, {}, self.clock).mark('s3', {})
    def test_empty_not_measurement(self):
        self.assertIsNone(Ledger(self.path, {}, self.clock).summary()['seconds_first_execute_to_external_receipt'])
    def test_only_backend_annotation_source(self):
        old = {'repoURL': 'r', 'targetRevision': 'c', 'kustomize': {'patches': []}}
        new = desired_source(old, 'run')
        self.assertEqual(old['kustomize']['patches'], [])
        patch = json.loads(new['kustomize']['patches'][-1]['patch'])
        self.assertEqual(patch['metadata']['name'], 'backend')
        self.assertNotIn('containers', patch['spec']['template'])
    def test_sync_no_prune_retry(self):
        app = {'metadata': {'uid': 'u', 'resourceVersion': '1'}, 'spec': {}}
        patch = operation_patch(app, {'targetRevision': 'r'})
        self.assertFalse(patch[-1]['value']['sync']['prune'])
        self.assertEqual(patch[-1]['value']['retry']['limit'], 0)
    def deployments(self):
        return {name: {'metadata': {'uid': name}, 'spec': {'replicas': 3, 'template': {'metadata': {}, 'spec': {'containers': [{'image': 'sha256:fixed'}]}}}} for name in ('backend', 'frontend')}
    def test_frontend_change_rejected(self):
        a = self.deployments();b = copy.deepcopy(a);b['frontend']['spec']['template']['spec']['containers'][0]['image'] = 'other'
        with self.assertRaisesRegex(RuntimeError, 'FRONTEND'): templates(a, b)
    def test_backend_image_change_rejected(self):
        a = self.deployments();b = copy.deepcopy(a);b['backend']['spec']['template']['spec']['containers'][0]['image'] = 'other'
        with self.assertRaisesRegex(RuntimeError, 'ANNOTATION'): templates(a, b)
    def test_backend_annotation_only_accepted(self):
        a = self.deployments();b = copy.deepcopy(a);b['backend']['spec']['template']['metadata']['annotations'] = {'seokpan.io/presentation-rollout': 'run'}
        templates(a, b)
    def sample(self, grant='GRANT SELECT ON stone_game.* TO observer'):
        return parse('Ssl_cipher\tTLS_AES_256_GCM_SHA384\nGLOBAL_LIMIT\t68\t0\nThreads_connected\t20\n'+grant+'\nACCOUNT\tidentity_svc\t5\n', ['identity_svc', 'game_svc'])
    def test_limited_process_visibility_not_zero(self):
        self.assertIsNone(self.sample()['per_account']['identity_svc'])
    def test_process_grant_allows_missing_account_zero(self):
        r = self.sample('GRANT SELECT, PROCESS ON *.* TO observer')
        self.assertEqual(r['per_account'], {'identity_svc': 5, 'game_svc': 0})
    def test_observed_headroom_no_double_reserve(self):
        r = observed_summary([{'monotonic_ns': 1, 'sample': self.sample()}])
        self.assertEqual(r['minimum_observed_headroom'], 48)
    def test_empty_samples_are_not_zero(self):
        self.assertIsNone(observed_summary([])['max_connections_observed'])
    def test_sample_gaps_and_errors_preserved(self):
        r = observed_summary([{'monotonic_ns': 1, 'sample': self.sample()}, {'monotonic_ns': 3, 'sample': None}, {'monotonic_ns': 10_000_000_001, 'sample': self.sample()}])
        self.assertEqual(r['failed_samples'], 1);self.assertEqual(r['actual_intervals_seconds'], [10])
    def test_budget_unknown_terminating_not_zero(self):
        g = {'pods': 3, 'processes_per_pod': 1, 'engines': {n: {'pool_size': 2, 'max_overflow': 0} for n in ('identity', 'game')}}
        c = {'schema_version': 1, 'input_kind': 'candidate', 'rds_max_connections': 68, 'reserved_connections': 10,
             'pod_groups': {n: copy.deepcopy(g) for n in ('steady', 'surge', 'terminating')}}
        c['pod_groups']['terminating']['pods'] = None
        self.assertEqual(budget_report(c)['result'], 'INPUT_INCOMPLETE')
    def test_no_observation_without_tls(self):
        with self.assertRaisesRegex(RuntimeError, 'TLS'): parse('GLOBAL_LIMIT\t68\t0\nThreads_connected\t1', [])
    def test_comparison_rejects_other_image(self):
        b = {'scenario': 'baseline', 'binding': {}, 'release': {'image': 'one'}, 'runtime': {}, 'workload': {}}
        r = {**b, 'scenario': 'rolling', 'release': {'image': 'two'}}
        with self.assertRaisesRegex(RuntimeError, 'SAME_BOUND'): compare(b, r, {}, {})
    def test_two_appenders_preserve_one_chain(self):
        a = Ledger(self.path, {}, self.clock);b = Ledger(self.path, {}, self.clock)
        a.mark('s0', {});b.mark('failure', {});a.mark('s1', {})
        self.assertEqual([x['event'] for x in Ledger(self.path, {}, self.clock).rows], ['s0', 'failure', 's1'])
    def test_unknown_trigger_is_not_resubmitted(self):
        work = Path(self.temp.name)/'roll';work.mkdir()
        deps = self.deployments()
        app = {'metadata': {'uid': 'app', 'resourceVersion': '1', 'annotations': {'seokpan.io/bootstrap-owner': 'cloud-app-held'}},
               'spec': {'source': {'targetRevision': 'sha'}, 'project': 'seokpan-cloud-app',
                        'destination': {'namespace': 'seokpan-cloud', 'server': 'https://kubernetes.default.svc'}},
               'status': {'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'}}}
        cfg = {'actor': 'tjung03', 'namespace': 'seokpan-cloud', 'application': 'seokpan-cloud-app',
               'oc': 'fake-oc', 'personal_kubeconfig': 'private', 'api': 'https://new:6443',
               'namespace_uid': 'ns', 'application_uid': 'app', 'run_id': 'test', 'not_before': '2000-01-01T00:00:00Z', 'not_after': '2099-01-01T00:00:00Z'}
        config = Path(self.temp.name)/'config.json';config.write_text(json.dumps(cfg))
        (work/'before.json').write_text(json.dumps({'config_sha256': sha(cfg), 'application': app,
            'source_after': desired_source(app['spec']['source'], 'test'), 'deployments': deps}))
        writes = []
        def fake(argv, **kwargs):
            if 'patch' in argv:
                writes.append(argv)
                return type('R', (), {'returncode': 1, 'stdout': b''})()
            if 'whoami' in argv: value = b'tjung03'
            elif 'config' in argv: value = json.dumps({'clusters': [{'cluster': {'server': cfg['api']}}]}).encode()
            elif 'namespace' in argv: value = b'{"metadata":{"uid":"ns"}}'
            elif 'application' in argv: value = json.dumps(app).encode()
            elif 'deployment' in argv: value = json.dumps(deps[argv[argv.index('deployment')+1]]).encode()
            else: raise AssertionError('unexpected command')
            return type('R', (), {'returncode': 0, 'stdout': value})()
        argv = ['rolling_control.py', 'trigger', '--config', str(config), '--work', str(work), '--execute']
        with patch('sys.argv', argv), patch('rolling_control.subprocess.run', fake), redirect_stdout(io.StringIO()):
            self.assertEqual(rolling_control.main(), 2)
            self.assertEqual(rolling_control.main(), 2)
        self.assertEqual(len(writes), 1)
        self.assertTrue((work/'trigger.STARTED.json').exists())
    def test_zero_account_limit_is_not_zero_connections(self):
        groups = {k: {'pods': 1, 'processes_per_pod': 1, 'engines': {e: {'pool_size': 2, 'max_overflow': 0}
            for e in ('identity', 'game')}} for k in ('steady', 'surge', 'terminating')}
        limits = {a: {'max_user_connections': 0, 'global_max_user_connections': 0, 'evidence_ref': 'current-sql'}
                  for a in ('identity_svc', 'game_svc')}
        r = account_budget({'pod_groups': groups}, limits)
        self.assertEqual(r['identity_svc']['configured_app_cap'], 6)
        self.assertTrue(r['identity_svc']['within_account_limit'])
    def test_current_per_account_cap_can_fail_even_if_global_fits(self):
        groups = {k: {'pods': 1, 'processes_per_pod': 1, 'engines': {e: {'pool_size': 2, 'max_overflow': 0}
            for e in ('identity', 'game')}} for k in ('steady', 'surge', 'terminating')}
        limits = {a: {'max_user_connections': 5, 'global_max_user_connections': 0, 'evidence_ref': 'current-sql'}
                  for a in ('identity_svc', 'game_svc')}
        self.assertFalse(account_budget({'pod_groups': groups}, limits)['game_svc']['within_account_limit'])
    def test_target_bound_is_metadata_not_numbered_stage(self):
        ledger = Ledger(self.path, {}, self.clock)
        ledger.mark('s0', {});ledger.mark('stage_target_bound', {'application_uid': 'a'})
        ledger.mark('s1', {})
        self.assertEqual(ledger.rows[1]['event'], 'stage_target_bound')
    def test_initial_api_failure_preserves_first_execute_time(self):
        work = Path(self.temp.name)
        config = {'run_id':'run','cluster_id':'new','namespace_uid':'ns','infra_sha':'i'}
        launch = SimpleNamespace(GITOPS='g', APP='seokpan-cloud-app', ARGO='openshift-gitops', digest=sha, normalize_source=lambda x:x,
            package=lambda *x:({'origin':'https://example.invalid'},config,{},None,{'images':{}}),
            oc_client=lambda *x:lambda *argv:(_ for _ in ()).throw(RuntimeError('READ_FAILED')),
            stage_app=lambda *x: self.fail('stage mutation called after failed target read'))
        p = SimpleNamespace(helpers=lambda:(launch,None))
        def fake_main():
            try:p.helpers()[0].stage_app(work/'package','single',{},None,'oc',work/'receipt',None)
            except RuntimeError:return 2
            return 0
        p.main=fake_main
        args=SimpleNamespace(infra=work,work=work,ledger=self.path,stage='single')
        factory=lambda path,binding:Ledger(path,binding,self.clock)
        with patch('stage_clock.module',return_value=p),patch('stage_clock.clock',self.clock),patch('stage_clock.Ledger',factory),patch('sys.argv',[]),redirect_stdout(io.StringIO()):
            self.assertEqual(stage_clock.entry(args,['--execute']),2)
        rows=[json.loads(x) for x in self.path.read_text().splitlines()]
        self.assertEqual([x['event'] for x in rows],['s0','failure'])
    def test_empty_db_summary_returns_nonzero_without_any_sql(self):
        import db_observation
        config=Path(self.temp.name)/'db-config.json';data=Path(self.temp.name)/'db.jsonl'
        config.write_text(json.dumps({'interval_seconds':5,'duration_seconds':180}));data.write_text('')
        with patch('sys.argv',['db_observation.py','summary','--config',str(config),'--out',str(data)]),redirect_stdout(io.StringIO()):
            self.assertEqual(db_observation.main(),2)
    def comparison_pair(self):
        binding={'application_uid':'app','namespace_uid':'ns','normalized_source_sha256':'source'}
        common={'binding':binding,'release':{n+'_digest':'sha256:fixed' for n in ('backend','frontend')},
                'runtime':{'replicas':{'backend':3,'frontend':3}},'workload':{},'status':'COMPLETED_OBSERVATION','reconnects':[]}
        b={**common,'run_id':'b','scenario':'baseline'}
        r={**common,'run_id':'r','scenario':'rolling','trigger_result':{'status':'REQUESTED_ONE_GITOPS_BACKEND_ROLL'}}
        bc={'run_id':'b','binding':binding};rc={'run_id':'r','binding':binding}
        v={'status':'PASS_BACKEND_ROLL_RECONCILED_NOT_BUSINESS_ACCEPTANCE','application_uid':'app','namespace_uid':'ns',
           'source_before_normalized_sha256':'source','images':{n:'repo@sha256:fixed' for n in ('backend','frontend')},
           'replicas':{'backend':3,'frontend':3}}
        return b,r,bc,rc,v
    def test_requested_roll_without_real_verification_cannot_form_pair(self):
        b,r,bc,rc,_=self.comparison_pair()
        with self.assertRaisesRegex(RuntimeError,'VERIFICATION_REQUIRED'):compare(b,r,bc,rc)
    def test_counts_from_other_run_cannot_form_pair(self):
        b,r,bc,rc,v=self.comparison_pair();rc['run_id']='other'
        with self.assertRaisesRegex(RuntimeError,'SAME_RUN'):compare(b,r,bc,rc,v)
    def test_verified_same_image_roll_can_form_scoped_pair(self):
        self.assertEqual(compare(*self.comparison_pair())['status'],'PAIRED_SCOPED_OBSERVATIONS')
    def test_checksum_changed_counts_file_is_rejected(self):
        import compare_runs,hashlib
        work=Path(self.temp.name)
        for name in ('receipt.json','counts.json'):(work/name).write_text('{}')
        hashes={name:hashlib.sha256((work/name).read_bytes()).hexdigest() for name in ('receipt.json','counts.json')}
        (work/'SHA256SUMS.json').write_text(json.dumps(hashes));(work/'counts.json').write_text('{"other":1}')
        with self.assertRaisesRegex(RuntimeError,'CHECKSUM'):compare_runs.load_run(work)
    def test_incomplete_pair_cli_returns_nonzero(self):
        import hashlib
        work=Path(self.temp.name);b,r,bc,rc,v=self.comparison_pair();b['status']='INCOMPLETE_OBSERVATION'
        for name,receipt,counts in (('baseline',b,bc),('rolling',r,rc)):
            d=work/name;d.mkdir();(d/'receipt.json').write_text(json.dumps(receipt));(d/'counts.json').write_text(json.dumps(counts))
            hashes={n:hashlib.sha256((d/n).read_bytes()).hexdigest() for n in ('receipt.json','counts.json')}
            (d/'SHA256SUMS.json').write_text(json.dumps(hashes))
        verification=work/'verify.json';verification.write_text(json.dumps(v))
        result=subprocess.run([sys.executable,'-I',str(Path(__file__).parent/'compare_runs.py'),
            '--baseline',str(work/'baseline'),'--rolling',str(work/'rolling'),'--rolling-verification',str(verification),'--out',str(work/'out.json')],capture_output=True)
        self.assertEqual(result.returncode,2);self.assertEqual(json.loads((work/'out.json').read_bytes())['status'],'INCOMPLETE_PAIRED_OBSERVATIONS')


if __name__ == '__main__': unittest.main()
