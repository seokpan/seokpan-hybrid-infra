"""New internal-registry admission and pre-Pod resume boundaries only."""
import copy
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


P = load('native_subject', HERE/'prestart.py')
R = load('resume_subject', HERE/'resume_native_pull.py')


class NativePull(unittest.TestCase):
    def fixture(self):
        name = P.SA+'-dockercfg-abc12'
        sa = {'metadata': {'name': P.SA, 'namespace': P.NS, 'uid': 'sa-current',
              'annotations': {'openshift.io/internal-registry-pull-secret-ref': name}},
              'imagePullSecrets': [{'name': name}]}
        secret = {'metadata': {'name': name, 'namespace': P.NS, 'uid': 'secret-current',
                  'ownerReferences': [{'apiVersion': 'v1', 'kind': 'ServiceAccount', 'name': P.SA, 'uid': 'sa-current'}]},
                  'type': 'kubernetes.io/dockercfg'}
        return sa, secret

    def test_actual_shape_platform_reference_allowed(self):
        sa, secret = self.fixture()
        self.assertEqual(P.platform_pull_refs(sa, lambda n: secret), sa['imagePullSecrets'])

    def test_legacy_and_extra_reference_denied(self):
        for name in ('seokpan-cloud-ecr-pull', 'other-dockercfg-abc12'):
            sa, secret = self.fixture()
            sa['imagePullSecrets'].append({'name': name})
            with self.assertRaises(P.Stop):
                P.platform_pull_refs(sa, lambda n: secret)

    def test_old_owner_type_and_annotation_denied(self):
        for change in ('owner', 'type', 'annotation', 'namespace'):
            sa, secret = self.fixture()
            if change == 'owner': secret['metadata']['ownerReferences'][0]['uid'] = 'old-sa'
            if change == 'type': secret['type'] = 'Opaque'
            if change == 'annotation': sa['metadata']['annotations'] = {}
            if change == 'namespace': secret['metadata']['namespace'] = 'other'
            with self.assertRaises(P.Stop):
                P.platform_pull_refs(sa, lambda n: secret)

    def test_only_bound_admitted_pod_reference_allowed(self):
        sa, secret = self.fixture()
        refs = P.platform_pull_refs(sa, lambda n: secret)
        wanted = P.pod_spec('data-admin-abcdef', 'abcdef', 'registry.example/backend@sha256:'+'a'*64)
        live = copy.deepcopy(wanted)
        live['metadata']['uid'] = 'pod-current'
        live['spec']['imagePullSecrets'] = refs
        self.assertEqual(P.validate_pod(live, wanted, platform_refs=refs), 'pod-current')
        with self.assertRaises(P.Stop): P.validate_pod(live, wanted)
        live['spec']['imagePullSecrets'].append({'name': 'seokpan-cloud-ecr-pull'})
        with self.assertRaises(P.Stop): P.validate_pod(live, wanted, platform_refs=refs)

    def records(self):
        old = {'cluster_id': 'same', 'input_sha256': 'old'}
        new = {'cluster_id': 'same', 'input_sha256': 'new'}
        records = {'binding.json': old,
                   'runtime-inputs.json': {'binding': old, 'result': {'status': 'PASS_CURRENT_RUNTIME_INPUTS_CONNECTED', 'data_updates': 4}},
                   'new-inputs.started.json': {'binding': old, 'started_at_utc': 'original'}}
        return records, old, new

    def test_success_and_failed_attempt_preserved_during_binding_change(self):
        records, old, new = self.records()
        result = R.migrate_records(records, old, new)
        self.assertEqual(result['runtime-inputs.json']['result'], records['runtime-inputs.json']['result'])
        self.assertEqual(result['new-inputs.started.json']['started_at_utc'], 'original')
        self.assertEqual(result['runtime-inputs.json']['binding'], new)
        self.assertEqual(records['runtime-inputs.json']['binding'], old)

    def test_later_unknown_or_mismatched_records_rejected(self):
        for change in ('later', 'binding', 'status', 'missing'):
            records, old, new = self.records()
            if change == 'later': records['new-pod.started.json'] = {'binding': old}
            if change == 'binding': records['new-inputs.started.json']['binding'] = {'other': True}
            if change == 'status': records['runtime-inputs.json']['result']['status'] = 'STOP'
            if change == 'missing': del records['runtime-inputs.json']
            with self.assertRaises(RuntimeError): R.migrate_records(records, old, new)


if __name__ == '__main__':
    unittest.main()
