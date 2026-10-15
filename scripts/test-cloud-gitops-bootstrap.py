import copy
import importlib.util
import json
from pathlib import Path
import unittest
import contextlib
import io
import os
import sys
import tempfile
import types
from unittest.mock import patch

path = Path(__file__).with_name('bootstrap-cloud-gitops-20261015.py')
if not path.exists():
    path = Path(__file__).with_name('bootstrap-cloud-gitops.py')
spec = importlib.util.spec_from_file_location('bootstrap', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def package():
    return {'items': [{'metadata': {'name': m.PACKAGE}, 'status': {
        'catalogSource': 'redhat-operators', 'catalogSourceNamespace': 'openshift-marketplace',
        'channels': [{'name': m.CHANNEL, 'currentCSV': m.CSV}]}}]}


def plan():
    return {'metadata': {'name': 'install-test', 'uid': '123', 'resourceVersion': '17'},
            'spec': {'approval': 'Manual', 'approved': False, 'clusterServiceVersionNames': [m.CSV]},
            'status': {'phase': 'RequiresApproval', 'bundleLookups': [{'identifier': m.CSV,
                        'catalogSourceRef': {'name': 'redhat-operators', 'namespace': 'openshift-marketplace'}}]}}


class Checks(unittest.TestCase):
    def test_exact_catalog(self):
        self.assertEqual(m.catalog_candidate(package())['starting_csv'], m.CSV)

    def test_historical_entry_is_exact_version(self):
        p = package()
        c = p['items'][0]['status']['channels'][0]
        c.update(currentCSV='openshift-gitops-operator.v1.21.5', entries=[{'name': m.CSV, 'version': m.VERSION}])
        self.assertEqual(m.catalog_candidate(p)['starting_csv'], m.CSV)

    def test_no_latest_fallback(self):
        p = package()
        p['items'][0]['status']['channels'][0]['currentCSV'] = 'openshift-gitops-operator.v1.21.5'
        with self.assertRaisesRegex(m.Stop, 'NO_VERSION_FALLBACK'):
            m.catalog_candidate(p)

    def test_wrong_historical_version(self):
        p = package()
        p['items'][0]['status']['channels'][0].update(currentCSV='other', entries=[{'name': m.CSV, 'version': '1.21.5'}])
        with self.assertRaises(m.Stop):
            m.catalog_candidate(p)

    def test_untrusted_or_duplicate_catalog(self):
        for field, value in [('catalogSource', 'community-operators'), ('catalogSourceNamespace', 'other')]:
            p = package()
            p['items'][0]['status'][field] = value
            with self.assertRaises(m.Stop):
                m.catalog_candidate(p)
        p = package()
        p['items'] *= 2
        with self.assertRaises(m.Stop):
            m.catalog_candidate(p)

    def test_owned_resources_accepted(self):
        for obj in m.manifests():
            m.same_owned(copy.deepcopy(obj), obj)

    def test_no_takeover_or_spec_change(self):
        for field in ('ownership', 'spec'):
            obj = m.manifests()[2]
            changed = copy.deepcopy(obj)
            if field == 'ownership':
                changed['metadata']['labels'] = {}
            else:
                changed['spec']['installPlanApproval'] = 'Automatic'
            with self.assertRaises(m.Stop):
                m.same_owned(changed, obj)

    def test_exact_installplan(self):
        for phase in ('RequiresApproval', 'Installing', 'Complete'):
            p = plan()
            p['status']['phase'] = phase
            m.exact_plan(p, {'name': 'install-test', 'uid': '123'})

    def test_unknown_dependency_and_version_rejected(self):
        for versions in ([], [m.CSV, 'another-operator'], ['other-version']):
            p = plan()
            p['spec']['clusterServiceVersionNames'] = versions
            with self.assertRaises(m.Stop):
                m.exact_plan(p, {'name': 'install-test', 'uid': '123'})

    def test_reference_race_rejected(self):
        with self.assertRaises(m.Stop):
            m.exact_plan(plan(), {'name': 'install-test', 'uid': 'replaced'})

    def test_untrusted_bundle_rejected(self):
        for value in ([], [{'identifier': m.CSV, 'catalogSourceRef': {'name': 'community-operators', 'namespace': 'openshift-marketplace'}}]):
            p = plan()
            p['status']['bundleLookups'] = value
            with self.assertRaises(m.Stop):
                m.exact_plan(p, {'name': 'install-test', 'uid': '123'})

    def test_failed_or_unresolved_plan_rejected(self):
        for phase in ('Failed', 'Planning', None):
            p = plan()
            p['status']['phase'] = phase
            with self.assertRaises(m.Stop):
                m.exact_plan(p, {'name': 'install-test', 'uid': '123'})

    def test_wait_finishes_without_mutation_replay(self):
        with patch.object(m.time, 'sleep'):
            action = unittest.mock.Mock(side_effect=[None, {'status': 'ready'}])
            self.assertEqual(m.wait_for(action, 5, 'TEST'), {'status': 'ready'})
            self.assertEqual(action.call_count, 2)

    def test_wait_timeout_preserves_state(self):
        with patch.object(m.time, 'monotonic', side_effect=[0, 10]):
            with self.assertRaisesRegex(m.Stop, 'PRESERVE_PARTIAL_STATE'):
                m.wait_for(lambda: None, 5, 'TEST')

    def test_ansible_entrypoint_syntax(self):
        p = json.loads(m.PLAYBOOK)
        self.assertEqual(p[0]['connection'], 'local')
        argv = p[0]['tasks'][0]['ansible.builtin.command']['argv']
        self.assertIn('create', argv)
        self.assertNotIn('apply', argv)

    def run_batch(self, invalid_version=False, extra_dependency=False):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root = home / '.local/share/seokpan-rosa-plan-inputs-fixture'
            mgmt = root / 'management/2tejeb2farjaaoef1e74asdf6amrtpu3'
            prior = root / 'cloud-independent-fixture'
            cli = home / '.local/share/seokpan/oc-cli-4.20'
            for d in (mgmt, prior, cli, root / 'protected'):
                d.mkdir(parents=True, exist_ok=True)
            (root / 'protected/inputs.tfvars.json').write_text('{}')
            (root / 'b01-b05-b08-independent-attempt.json').write_text(json.dumps({'result_directory': str(prior)}))
            (prior / 'summary.json').write_text(json.dumps({'status': 'INDEPENDENT_BATCH_COMPLETED',
                'steps': {'B08_management_inventory': {'argoproj_api_resources': 0}}}))
            (mgmt / 'data-platform-handoff.json').write_text(json.dumps({'api_url': 'https://fixture'}))
            (cli / 'oc').write_text('fixture')
            real_digest = m.digest
            (cli / 'receipt.json').write_text(json.dumps({'oc_sha256': real_digest(cli / 'oc')}))
            calls = []
            class FakeRunner:
                def __init__(self, oc, kube, folder):
                    self.env, self.counter = {}, 0
                def call(self, args, label, payload=None):
                    self.counter += 1
                    calls.append(args)
                    if args[0] == 'whoami':
                        return b'https://fixture'
                    if args[0] == 'api-resources':
                        return b''
                    if args[:2] == ['get', 'subscriptions.operators.coreos.com']:
                        return b'{"items": []}'
                    if args[0] == 'patch':
                        return b'patched'
                    raise AssertionError(args)
                def get(self, resource, name=None, namespace=None, optional=False):
                    self.counter += 1
                    if resource == 'packagemanifests':
                        value = package()
                        if invalid_version:
                            value['items'][0]['status']['channels'][0]['currentCSV'] = 'other'
                        return value
                    if resource == 'namespaces':
                        return None
                    if resource == 'routes.route.openshift.io':
                        return {'spec': {'host': 'oauth-openshift.apps.fixture.openshiftapps.com'}}
                    if resource == 'subscriptions.operators.coreos.com':
                        value = copy.deepcopy(m.manifests()[2])
                        value['status'] = {'installPlanRef': {'name': 'install-test', 'uid': '123'}}
                        return value
                    if resource == 'installplans.operators.coreos.com':
                        value = plan()
                        if extra_dependency:
                            value['spec']['clusterServiceVersionNames'].append('unreviewed')
                        return value
                    if resource == 'clusterserviceversions.operators.coreos.com':
                        return {'spec': {'version': m.VERSION}, 'status': {'phase': 'Succeeded'}}
                    if resource == 'argocds.argoproj.io':
                        return {'status': {'phase': 'Available'}}
                    raise AssertionError(resource)
            def fake_process(args, **kwargs):
                calls.append(args)
                self.assertEqual(args[0], 'ansible-playbook')
                variables = json.loads(Path(args[-1][1:]).read_text())
                docs = [json.loads(Path(p).read_text()) for p in variables['manifest_paths']]
                self.assertEqual([v['kind'] for v in docs], ['Namespace', 'OperatorGroup', 'Subscription'])
                return types.SimpleNamespace(returncode=0)
            patches = [patch.object(m, 'Path', type(home)), patch.object(type(home), 'home', return_value=home),
                patch.object(m.os, 'name', 'posix'), patch.object(m.os, 'getuid', return_value=1, create=True),
                patch.object(m.os, 'O_NOFOLLOW', 0, create=True),
                patch.dict(sys.modules, {'pwd': types.SimpleNamespace(getpwuid=lambda _: types.SimpleNamespace(pw_name='jth')),
                    'fcntl': types.SimpleNamespace(flock=lambda *args: None, LOCK_EX=1, LOCK_NB=2)}),
                patch.object(m.shutil, 'which', return_value='ansible-playbook'),
                patch.object(m, 'digest', side_effect=lambda p: m.INPUT_SHA if p.name == 'inputs.tfvars.json' else real_digest(p)),
                patch.object(m, 'Runner', FakeRunner), patch.object(m.subprocess, 'run', side_effect=fake_process),
                patch.object(sys, 'argv', ['bootstrap', str(root)])]
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                code = m.main()
            receipt = json.loads(next(root.glob('cloud-gitops-bootstrap-*/receipt.json')).read_text())
            return code, receipt, calls

    def test_complete_bootstrap_without_app_or_auth_changes(self):
        code, receipt, calls = self.run_batch()
        self.assertEqual(code, 0)
        self.assertEqual(receipt['status'], 'PASS_CLOUD_GITOPS_OPERATOR_AND_DEFAULT_ARGO')
        self.assertFalse(receipt['application_created'])
        self.assertFalse(receipt['human_idp_or_human_rolebinding_changes'])
        self.assertFalse(receipt['terraform_or_sql_changes'])
        self.assertEqual(len([x for x in calls if x[0] == 'ansible-playbook']), 1)
        self.assertEqual(len([x for x in calls if x[0] == 'patch']), 1)

    def test_unavailable_version_no_install(self):
        code, receipt, calls = self.run_batch(invalid_version=True)
        self.assertEqual(code, 1)
        self.assertIn('NO_VERSION_FALLBACK', receipt['reason'])
        self.assertFalse(any(x[0] in ('ansible-playbook', 'patch') for x in calls))

    def test_unreviewed_dependency_not_approved(self):
        code, receipt, calls = self.run_batch(extra_dependency=True)
        self.assertEqual(code, 1)
        self.assertEqual(receipt['reason'], 'UNEXPECTED_INSTALLPLAN_VERSION_OR_DEPENDENCIES')
        self.assertFalse(any(x[0] == 'patch' for x in calls))


class AtomicApproval(unittest.TestCase):
    ref = {'name': 'install-test', 'uid': '123'}

    def run_approval(self, fresh=None, race=None):
        current = copy.deepcopy(fresh or plan())
        accepted = []
        class Runner:
            def get(self, *args):
                return copy.deepcopy(current)
            def call(self, args, label):
                if race:
                    race(current)
                patch = json.loads(args[args.index('-p') + 1])
                for operation in patch:
                    if operation['op'] == 'test':
                        value = current
                        for field in operation['path'].split('/')[1:]:
                            value = value[field]
                        if value != operation['value']:
                            raise m.Stop('ATOMIC_INSTALLPLAN_TEST_FAILED')
                current['spec']['approved'] = True
                accepted.append(True)
        m.approve_exact_installplan(Runner(), self.ref)
        return accepted

    def test_patch_has_uid_version_and_exact_spec_guards(self):
        patch = m.approval_patch(plan(), self.ref)
        tests = {p['path']: p['value'] for p in patch if p['op'] == 'test'}
        self.assertEqual(tests, {'/metadata/uid': '123', '/metadata/resourceVersion': '17',
            '/spec/approval': 'Manual', '/spec/clusterServiceVersionNames': [m.CSV],
            '/spec/approved': False})
        self.assertEqual(self.run_approval(), [True])

    def test_reread_different_csv_never_approved(self):
        fresh = plan(); fresh['spec']['clusterServiceVersionNames'] = ['unreviewed']
        with self.assertRaisesRegex(m.Stop, 'UNEXPECTED_INSTALLPLAN'):
            self.run_approval(fresh)

    def test_reread_different_bundle_never_approved(self):
        fresh = plan(); fresh['status']['bundleLookups'][0]['catalogSourceRef']['name'] = 'other'
        with self.assertRaisesRegex(m.Stop, 'OFFICIAL_BUNDLE'):
            self.run_approval(fresh)

    def test_same_uid_csv_changed_after_read_rejected_atomically(self):
        with self.assertRaisesRegex(m.Stop, 'ATOMIC_INSTALLPLAN_TEST_FAILED'):
            self.run_approval(race=lambda p: p['spec']['clusterServiceVersionNames'].append('extra'))

    def test_approval_mode_changed_after_read_rejected_atomically(self):
        with self.assertRaisesRegex(m.Stop, 'ATOMIC_INSTALLPLAN_TEST_FAILED'):
            self.run_approval(race=lambda p: p['spec'].update(approval='Automatic'))

    def test_status_change_after_read_rejected_by_resource_version(self):
        def change(p):
            p['metadata']['resourceVersion'] = '18'
            p['status']['bundleLookups'][0]['catalogSourceRef']['name'] = 'other'
        with self.assertRaisesRegex(m.Stop, 'ATOMIC_INSTALLPLAN_TEST_FAILED'):
            self.run_approval(race=change)

    def test_missing_resource_version_refused(self):
        fresh = plan(); fresh['metadata'].pop('resourceVersion')
        with self.assertRaisesRegex(m.Stop, 'RESOURCE_VERSION_REQUIRED'):
            m.approval_patch(fresh, self.ref)

    def test_already_approved_exact_plan_not_patched_again(self):
        fresh = plan(); fresh['spec']['approved'] = True
        self.assertEqual(self.run_approval(fresh), [])


if __name__ == '__main__':
    unittest.main()

