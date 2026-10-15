import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

path = Path(__file__).with_name('rosa-independent-checks.py')
spec = importlib.util.spec_from_file_location('independent', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ACCOUNT = '123456789012'
CLUSTER = 'fixturecluster123456'


def objects():
    out = []
    for component in ('backend', 'frontend'):
        out.append({'kind': 'Deployment', 'metadata': {'name': component, 'namespace': m.NS},
                    'spec': {'replicas': 0, 'template': {'spec': {
                        'serviceAccountName': m.SA, 'automountServiceAccountToken': False,
                        'imagePullSecrets': [{'name': m.PULL}],
                        'containers': [{'image': m.image_uri(ACCOUNT, component)}]}}}})
        out.append({'kind': 'Service', 'metadata': {'name': component, 'namespace': m.NS}})
    for name in ('frontend', 'backend-api', 'backend-ws'):
        out.append({'kind': 'Route', 'metadata': {'name': name, 'namespace': m.NS},
                    'spec': {'tls': {'termination': 'edge', 'insecureEdgeTerminationPolicy': 'Redirect'}}})
    out.append({'kind': 'ConfigMap', 'metadata': {'name': 'backend-config-abc123', 'namespace': m.NS}})
    return out


class RenderTests(unittest.TestCase):
    def test_exact_eight_zero_replica_objects(self):
        m.validate_render(objects(), ACCOUNT)

    def test_nonzero_backend_rejected(self):
        x = objects(); x[0]['spec']['replicas'] = 1
        with self.assertRaisesRegex(m.Stop, 'MUST_REMAIN_ZERO'): m.validate_render(x, ACCOUNT)

    def test_nonzero_frontend_rejected(self):
        x = objects(); x[2]['spec']['replicas'] = 1
        with self.assertRaises(m.Stop): m.validate_render(x, ACCOUNT)

    def test_different_namespace_rejected(self):
        x = objects(); x[0]['metadata']['namespace'] = 'default'
        with self.assertRaisesRegex(m.Stop, 'NAMESPACE'): m.validate_render(x, ACCOUNT)

    def test_duplicate_object_rejected(self):
        x = objects(); x[3] = copy.deepcopy(x[1])
        with self.assertRaisesRegex(m.Stop, 'DUPLICATE'): m.validate_render(x, ACCOUNT)

    def test_extra_resource_rejected(self):
        x = objects(); x.append({'kind': 'Namespace'})
        with self.assertRaises(m.Stop): m.validate_render(x, ACCOUNT)

    def test_wrong_kind_rejected(self):
        x = objects(); x[-1]['kind'] = 'Secret'
        with self.assertRaises(m.Stop): m.validate_render(x, ACCOUNT)

    def test_other_image_rejected(self):
        x = objects(); x[2]['spec']['template']['spec']['containers'][0]['image'] = 'busybox:latest'
        with self.assertRaisesRegex(m.Stop, 'IMAGE'): m.validate_render(x, ACCOUNT)

    def test_extra_sidecar_rejected(self):
        x = objects(); x[0]['spec']['template']['spec']['containers'].append({'image': 'x'})
        with self.assertRaises(m.Stop): m.validate_render(x, ACCOUNT)

    def test_mount_token_rejected(self):
        x = objects(); x[0]['spec']['template']['spec']['automountServiceAccountToken'] = True
        with self.assertRaises(m.Stop): m.validate_render(x, ACCOUNT)

    def test_other_sa_rejected(self):
        x = objects(); x[0]['spec']['template']['spec']['serviceAccountName'] = 'default'
        with self.assertRaises(m.Stop): m.validate_render(x, ACCOUNT)

    def test_other_pull_secret_rejected(self):
        x = objects(); x[0]['spec']['template']['spec']['imagePullSecrets'] = [{'name': 'admin-pull'}]
        with self.assertRaises(m.Stop): m.validate_render(x, ACCOUNT)

    def test_insecure_route_rejected(self):
        x = objects(); x[4]['spec']['tls']['insecureEdgeTerminationPolicy'] = 'Allow'
        with self.assertRaisesRegex(m.Stop, 'ROUTE_TLS'): m.validate_render(x, ACCOUNT)

    def test_changed_ca_not_hidden_by_defaults(self):
        self.assertIn('SSLContext(ssl.PROTOCOL_TLS_CLIENT)', m.NEGATIVE_PROBE)
        self.assertNotIn('create_default_context', m.NEGATIVE_PROBE)


class ParserProbeTests(unittest.TestCase):
    def test_json_list(self):
        self.assertEqual(m.json_stream(json.dumps({'kind': 'List', 'items': objects()}).encode()), objects())

    def test_json_stream_all_eight(self):
        self.assertEqual(m.json_stream(('\n'.join(json.dumps(x) for x in objects())).encode()), objects())

    def test_empty_rejected(self):
        with self.assertRaises(m.Stop): m.json_stream(b'  ')

    def test_broken_json_rejected(self):
        with self.assertRaises(json.JSONDecodeError): m.json_stream(b'{')

    def test_scalar_rejected(self):
        with self.assertRaises(m.Stop): m.json_stream(b'42')

    def test_probe_does_not_start_servers(self):
        pod = m.probe_object('owned-probe', ACCOUNT, '1234')
        self.assertNotIn('app', pod['metadata']['labels'])
        for c in pod['spec']['containers']:
            self.assertIn('sleep', ' '.join(c['command']))
            self.assertFalse(c['securityContext']['allowPrivilegeEscalation'])
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertNotIn('env', c)
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        self.assertEqual(pod['spec']['activeDeadlineSeconds'], 600)

    def test_frontend_only_pull_check_backend_cache_reused(self):
        cs = m.probe_object('p', ACCOUNT, 'r')['spec']['containers']
        self.assertEqual(cs[0]['imagePullPolicy'], 'Always')
        self.assertEqual(cs[1]['imagePullPolicy'], 'IfNotPresent')

    def test_non_infra_node_affinity(self):
        pod = m.probe_object('p', ACCOUNT, 'r')['spec']
        expressions = pod['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchExpressions']
        self.assertEqual(len(expressions), 3)
        self.assertTrue(all(x['operator'] == 'DoesNotExist' for x in expressions))

    def test_session_scope_two_repos_and_explicit_denies(self):
        policy = m.session_policy(ACCOUNT)
        self.assertEqual(len(policy['Statement']), 4)
        self.assertEqual(len(policy['Statement'][1]['Resource']), 2)
        self.assertEqual(policy['Statement'][2]['Effect'], 'Deny')
        self.assertEqual(policy['Statement'][3]['Effect'], 'Deny')
        self.assertLess(len(json.dumps(policy, separators=(',', ':'))), 2048)
        self.assertNotIn('ecr:PutImage', str(policy))

    def test_invalid_account_rejected(self):
        with self.assertRaises(m.Stop): m.session_policy('*')

    def test_utc_times(self):
        self.assertEqual(m.time_value('2026-10-15T05:00:00Z').utcoffset().total_seconds(), 0)

    def test_naive_time_rejected(self):
        with self.assertRaises(m.Stop): m.time_value('2026-10-15T05:00:00')


def run_negative(ca_code=20, host_code=62, answer=b'-WRONGPASS invalid username-password pair\r\n', timeout=False, auth_timeout=False):
    class Sock:
        def __init__(self):
            greeting = b'\x0a8.0.0\x00' + b'\x00' * 13 + b'\x00\x08' + b'\x00' * 10
            self.buffer = len(greeting).to_bytes(3, 'little') + b'\x00' + greeting

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def recv(self, size):
            data, self.buffer = self.buffer[:size], self.buffer[size:]
            return data
        def sendall(self, data): pass
        def makefile(self, mode): return io.BytesIO(answer)

    class Context:
        def __init__(self, protocol): self.ca = None
        def load_verify_locations(self, cadata): self.ca = cadata
        def wrap_socket(self, sock, server_hostname):
            code = ca_code if self.ca == 'wrong-ca' else host_code if server_hostname == 'not-approved.invalid' else None
            if code is not None:
                e = ssl.SSLCertVerificationError(1, 'synthetic certificate failure'); e.verify_code = code; raise e
            if auth_timeout: raise TimeoutError('synthetic timeout')
            return sock

    def connect(*args, **kwargs):
        if timeout: raise TimeoutError('synthetic timeout')
        return Sock()
    fake_ssl = types.SimpleNamespace(SSLContext=Context, PROTOCOL_TLS_CLIENT=ssl.PROTOCOL_TLS_CLIENT,
                                     TLSVersion=ssl.TLSVersion, SSLCertVerificationError=ssl.SSLCertVerificationError)
    fake_socket = types.SimpleNamespace(create_connection=connect)
    payload = {'rds_address': 'rds.fixture.invalid', 'redis_address': 'redis.fixture.invalid',
               'rds_ca': 'rds-ca', 'redis_ca': 'redis-ca', 'untrusted_ca': 'wrong-ca', 'wrong_token': 'wrong'}
    stdout = io.StringIO()
    with patch.dict(sys.modules, {'ssl': fake_ssl, 'socket': fake_socket}), patch('sys.stdin', io.StringIO(json.dumps(payload))), patch('sys.stdout', stdout):
        exec(m.NEGATIVE_PROBE, {})
    return json.loads(stdout.getvalue())


class NegativeTests(unittest.TestCase):
    def test_five_rejections_and_zero_data_commands(self):
        out = run_negative()
        self.assertEqual(len(out['checks']), 5)
        self.assertTrue(all(x['status'] == 'PASS' for x in out['checks'].values()))
        self.assertEqual(out['sql_queries'], 0); self.assertEqual(out['redis_data_commands'], 0)

    def test_network_timeout_not_accepted_as_rejection(self):
        self.assertTrue(all(x['status'] != 'PASS' for x in run_negative(timeout=True)['checks'].values()))

    def test_wrong_ca_hostname_error_not_accepted(self):
        self.assertEqual(run_negative(ca_code=62)['checks']['rds_wrong_ca']['status'], 'FAIL_OTHER_CERTIFICATE_ERROR')

    def test_wrong_host_ca_error_not_accepted(self):
        self.assertEqual(run_negative(host_code=20)['checks']['valkey_wrong_hostname']['status'], 'FAIL_OTHER_CERTIFICATE_ERROR')

    def test_expired_cert_not_ca_or_hostname_pass(self):
        out = run_negative(ca_code=10, host_code=10)['checks']
        self.assertTrue(all(x['status'] != 'PASS' for k, x in out.items() if k != 'valkey_wrong_auth'))

    def test_auth_unexpectedly_accepted_is_failure(self):
        self.assertNotEqual(run_negative(answer=b'+OK\r\n')['checks']['valkey_wrong_auth']['status'], 'PASS')

    def test_auth_noauth_is_not_wrongpass(self):
        self.assertNotEqual(run_negative(answer=b'-NOAUTH Authentication required\r\n')['checks']['valkey_wrong_auth']['status'], 'PASS')

    def test_auth_cannot_reach_server_not_pass(self):
        self.assertEqual(run_negative(auth_timeout=True)['checks']['valkey_wrong_auth']['status'], 'FAIL_AUTH_NOT_REACHED')


class PullLeaseTests(unittest.TestCase):
    def test_current_lease_reused_without_aws(self):
        r = types.SimpleNamespace(get=lambda *a: {'type':'kubernetes.io/dockerconfigjson',
            'metadata': {'labels':m.LABELS,'annotations':{'seokpan.io/pull-expires-at':'2099-01-01T00:00:00Z'}}})
        with patch.object(m,'aws') as api:
            out=m.pull_lease(r,ACCOUNT)
        self.assertEqual(out['status'],'REUSED_CURRENT_LEASE');api.assert_not_called()

    def test_expired_lease_renews_only_existing_secret(self):
        writes=[]
        r=types.SimpleNamespace(env={},get=lambda *a: {'type':'kubernetes.io/dockerconfigjson','metadata':{'labels':m.LABELS,'annotations':{}}},
            call=lambda args,label,payload:writes.append((args,label,json.loads(payload))))
        def fake(args,env,label):
            if label=='PULL_HUMAN_CALLER':return {'Arn':'arn:aws:iam::'+ACCOUNT+':user/tjung'}
            if label=='PULL_READ_ONLY_FEDERATION':
                name=args[args.index('--name')+1]
                return {'FederatedUser':{'Arn':'arn:aws:sts::'+ACCOUNT+':federated-user/'+name},
                        'Credentials':{'AccessKeyId':'synthetic-id','SecretAccessKey':'synthetic-key',
                                       'SessionToken':'synthetic-token','Expiration':'2099-01-01T00:00:00Z'}}
            if label=='PULL_SESSION_CALLER':
                name=recorded_names[-1]
                return {'Arn':'arn:aws:sts::'+ACCOUNT+':federated-user/'+name}
            if label=='PULL_ECR_TOKEN':return {'authorizationData':[{'proxyEndpoint':'https://'+ACCOUNT+'.dkr.ecr.ap-northeast-2.amazonaws.com',
                'authorizationToken':base64.b64encode(b'AWS:synthetic-token').decode(),'expiresAt':'2099-01-01T00:00:00Z'}]}
            raise AssertionError(label)
        recorded_names=[]
        def api(args,env,label):
            if label=='PULL_READ_ONLY_FEDERATION':recorded_names.append(args[args.index('--name')+1])
            return fake(args,env,label)
        with patch.object(m.shutil,'which',return_value='aws'),patch.object(m,'aws',side_effect=api):
            out=m.pull_lease(r,ACCOUNT)
        self.assertEqual(out['iam_changes'],0);self.assertEqual(len(writes),1)
        args,_,obj=writes[0]
        self.assertEqual(obj['kind'],'Secret');self.assertEqual(obj['metadata']['name'],m.PULL)
        self.assertIn('--server-side',args);self.assertNotIn('last-applied-configuration',json.dumps(obj))
        self.assertNotIn('synthetic',json.dumps(out))

    def test_other_human_or_role_rejected_without_credential_fallback(self):
        r=types.SimpleNamespace(env={},get=lambda *a: {'type':'kubernetes.io/dockerconfigjson','metadata':{'labels':m.LABELS,'annotations':{}}})
        with patch.object(m.shutil,'which',return_value='aws'),patch.object(m,'aws',return_value={'Arn':'arn:aws:sts::'+ACCOUNT+':assumed-role/someone/x'}) as api:
            with self.assertRaisesRegex(m.Stop,'EXPECTED_TJUNG'):m.pull_lease(r,ACCOUNT)
        self.assertEqual(api.call_count,1)

    def test_foreign_secret_not_overwritten(self):
        r=types.SimpleNamespace(get=lambda *a: {'type':'kubernetes.io/dockerconfigjson','metadata':{'labels':{},'annotations':{}}})
        with self.assertRaisesRegex(m.Stop,'PULL_SECRET_SCOPE'):m.pull_lease(r,ACCOUNT)


class BatchTests(unittest.TestCase):
    def exercise(self, failure=None):
        calls = []
        state = {'pod': None}
        class FakeRunner(m.Runner):
            def call(self, args, label, payload=None, **kwargs):
                self.calls += 1; calls.append((args, label, payload))
                if label == 'target-server': raw = b'https://api.fixture.invalid:6443\n'
                elif label == 'held-server-admission':
                    if failure == 'admission': raise m.Stop('ADMISSION_REJECTED')
                    raw = json.dumps({'kind': 'List', 'items': objects()}).encode()
                elif label == 'create-independent-probe':
                    state['pod'] = json.loads(payload); state['pod']['metadata']['uid'] = 'fixture-uid'
                    state['pod']['spec']['nodeName'] = 'fixture-worker'
                    state['pod']['status'] = {'containerStatuses': [
                        {'name': n, 'state': {'running': {}}, 'imageID': 'fixture@sha256:123'}
                        for n in ('frontend-pull', 'negative-probe')]}
                    # Real status.running is a nonempty dict with startedAt.
                    for c in state['pod']['status']['containerStatuses']: c['state']['running'] = {'startedAt': 'fixture'}
                    if failure == 'ambiguous-create': raise m.Stop('CREATE_RESPONSE_LOST')
                    raw = json.dumps(state['pod']).encode()
                elif label == 'negative-only-results': raw = json.dumps(run_negative()).encode()
                elif label == 'current-management-identity': raw = b'cluster-admin\n'
                elif label == 'current-cluster-admin': raw = b'yes\n'
                elif label == 'argo-api-discovery': raw = b''
                elif label == 'owned-probe-delete': state['pod'] = None; raw = b'deleted'
                else: raise AssertionError('Unexpected call '+label)
                return subprocess.CompletedProcess(args, 0, raw, b'')

            def get(self, args, label):
                self.calls += 1; calls.append((args, label, None))
                if label == 'namespace-ownership': return {'metadata': {'labels': m.LABELS}}
                if label == 'runtime-service-account': return {'automountServiceAccountToken': False, 'imagePullSecrets': [{'name': m.PULL}]}
                if label == 'oauth-inventory': return {'spec': {'identityProviders': []}}
                if label in ('namespace-rolebindings', 'cluster-rolebindings'): return {'items': []}
                if label == 'pull-secret-metadata': return {'type': 'kubernetes.io/dockerconfigjson', 'metadata': {'labels': m.LABELS,
                    'annotations': {'seokpan.io/pull-expires-at': '2099-10-15T05:00:00Z'}}}
                if label in ('probe-status', 'owned-probe-cleanup-check'):
                    pod = copy.deepcopy(state['pod'])
                    if label == 'owned-probe-cleanup-check' and failure == 'uid-changed' and pod: pod['metadata']['uid'] = 'someone-else'
                    return pod
                if label == 'frontend-compute-node': return {'metadata': {'labels': {'node-role.kubernetes.io/worker': ''}}}
                if label in ('database-existing-ca', 'redis-existing-ca'): return {'data': {'ca.crt': 'fixture-cert'}}
                if label == 'existing-redis-token-for-nonmatch': return {'data': {'SEOKPAN_REDIS_AUTH_TOKEN': base64.b64encode(b'correct-secret-not-printed').decode()}}
                raise AssertionError('Unexpected get '+label)
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            root = home/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'; root.mkdir(parents=True)
            (root/'protected').mkdir()
            raw = json.dumps({'foundation': {'account_id': ACCOUNT}}).encode()
            (root/'protected/inputs.tfvars.json').write_bytes(raw)
            management = root/'management'/CLUSTER; management.mkdir(parents=True)
            (management/'data-platform-handoff.json').write_text(json.dumps({'cluster_id': CLUSTER,'binding_enabled':True,'api_url':'https://api.fixture.invalid:6443'}))
            (management/'kubeconfig').write_text('private-kubeconfig')
            (management/'initial-admin.json').write_text('{}')
            ocdir = home/'.local/share/seokpan/oc-cli-4.20'; ocdir.mkdir(parents=True)
            (ocdir/'oc').write_bytes(b'oc-fixture')
            (ocdir/'receipt.json').write_text(json.dumps({'oc_sha256':m.sha(b'oc-fixture')}))
            connection = root/'cloud-connection-accepted'; connection.mkdir()
            (connection/'receipt.json').write_text('{}')
            (connection/'cloud-platform-inputs.json').write_text(json.dumps({'cluster_id':CLUSTER,'namespace':m.NS}))
            held = root/'cloud-held-render-tlg8gxme'; held.mkdir()
            (held/'held-render.yaml').write_bytes(b'accepted-render')
            (held/'held-render.json').write_text(json.dumps({'items':objects()}))
            (held/'receipt.json').write_text(json.dumps({'status':'PASS_PRIVATE_HELD_RENDER_CLIENT_VALIDATION',
                'render_sha256':m.sha(b'accepted-render'),'connection_receipt_sha256':m.sha(b'{}')}))
            data_raw = json.dumps({'rds_address':'fixture.rds.amazonaws.com','redis_primary_endpoint':'fixture.cache.amazonaws.com','rds_port':3306,'redis_port':6379}).encode()
            data_file = home/'c-data.json'; data_file.write_bytes(data_raw)
            original_regular = m.regular
            def mapped_regular(p):
                return data_file if str(p).endswith('c-data-outputs.json') else original_regular(p)
            stdout = io.StringIO()
            fake_fcntl = types.SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=lambda *a: None)
            with patch.object(m, 'controller_identity_ok', return_value=True), patch.object(m.Path, 'home', return_value=home), \
                 patch.object(m.os, 'O_NOFOLLOW', 0, create=True), patch.object(m.os, 'getuid', return_value=0, create=True), \
                 patch.dict(sys.modules, {'fcntl':fake_fcntl}), patch.object(m, 'INPUT_SHA', m.sha(raw)), \
                 patch.object(m, 'RENDER_SHA', m.sha(b'accepted-render')), patch.object(m, 'DATA_SHA',m.sha(data_raw)), \
                 patch.object(m, 'Runner', FakeRunner), patch.object(m, 'regular',side_effect=mapped_regular), \
                 patch.object(m.shutil, 'which', return_value='fixture'), \
                 patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([],0,b'-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----',b'')), \
                 patch.object(sys,'argv',['tool',str(root),'--held-render',str(held),'--data-file',str(data_file)]),patch('sys.stdout',stdout):
                code = m.main()
                summaries = list(root.glob('cloud-independent-*/summary.json'))
                out = json.loads(summaries[0].read_text())
                with self.assertRaisesRegex(m.Stop,'PREVIOUS_INDEPENDENT_ATTEMPT'):
                    m.main()
            return code,out,calls,stdout.getvalue()

    def test_full_batch_and_cleanup_without_real_api(self):
        code,out,calls,text = self.exercise()
        self.assertEqual(code,0)
        self.assertEqual(out['status'],'INDEPENDENT_BATCH_COMPLETED')
        self.assertEqual(out['temporary_pod_cleanup'],'PASS_REMOVED_OWN_PROBE')
        self.assertEqual(len(out['steps']['B01_rejection_checks']['checks']),5)
        self.assertNotIn('correct-secret-not-printed',text)
        self.assertFalse(out['app_started']); self.assertEqual(out['sql_queries'],0)
        applies=[a for a,_,_ in calls if a and a[0]=='apply']
        self.assertEqual(len(applies),1); self.assertIn('--dry-run=server',applies[0])
        self.assertEqual(len([1 for _,label,_ in calls if label=='create-independent-probe']),1)

    def test_server_failure_does_not_skip_other_independent_work(self):
        code,out,_,_=self.exercise('admission')
        self.assertEqual(code,1)
        self.assertEqual(out['steps']['B05_server_admission']['status'],'FAIL')
        self.assertEqual(out['steps']['B05_frontend_pull']['status'],'PASS')
        self.assertEqual(out['steps']['B01_rejection_checks']['status'],'PASS')
        self.assertEqual(out['steps']['B08_management_inventory']['changes'],0)

    def test_uid_change_blocks_deletion(self):
        code,out,calls,_=self.exercise('uid-changed')
        self.assertEqual(code,1);self.assertEqual(out['temporary_pod_cleanup'],'REQUIRES_ATTENTION')
        self.assertFalse(any(label=='owned-probe-delete' for _,label,_ in calls))

    def test_create_response_failure_still_cleans_owned_pod(self):
        code,out,calls,_=self.exercise('ambiguous-create')
        self.assertEqual(code,1);self.assertEqual(out['temporary_pod_cleanup'],'PASS_REMOVED_OWN_PROBE')
        self.assertTrue(any(label=='owned-probe-delete' for _,label,_ in calls))


if __name__ == '__main__':
    unittest.main()
