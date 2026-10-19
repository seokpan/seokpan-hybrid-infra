"""New launch bridge boundaries only: no old test suites, AWS, SQL or live cluster."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import cloud_launch as L


def inputs():
    return {'schema': 1, 'run_id': 'synthetic-launch', 'cluster_id': 'syntheticnewcluster0001',
            'api_url': 'https://api.fixture.p1.openshiftapps.com:6443',
            'namespace_uid': 'aaaaaaaa-1111-2222-3333-444444444444',
            'ingress_domain': 'apps.fixture.p1.openshiftapps.com',
            'database_host': 'db.example.test', 'redis_host': 'cache.example.test', 'infra_sha': 'a'*40,
            'secret_revision': 'synthetic-supply-01', 'release_review_ref': 'synthetic-review-01',
            'protected_uids': {kind+'/'+name: 'bbbbbbbb-1111-2222-3333-444444444444'
                               for kind, names in L.PROTECTED.items() for name in names},
            'window': {'not_before': '2030-01-01T09:00:00+09:00', 'not_after': '2030-01-01T17:00:00+09:00'},
            'budget': {'reported_spend_usd': 352.31, 'unreported_and_retained_allowance_usd': 10,
                       'hourly_upper_usd': 5, 'shutdown_reserve_hours': 2, 'variable_allowance_usd': 5,
                       'estimate_ref': 'synthetic-budget-not-current-price'}}


def app():
    return {'metadata': {'name': L.APP, 'namespace': L.ARGO, 'uid': 'current-app-uid', 'resourceVersion': '12',
                         'annotations': {'seokpan.io/bootstrap-owner': 'cloud-app-held'}},
            'spec': {'project': L.APP, 'destination': {'server': 'https://kubernetes.default.svc', 'namespace': L.NS},
                     'source': {'repoURL': L.REPO},
                     'syncPolicy': {'syncOptions': ['Prune=false', 'Delete=false', 'FailOnSharedResource=true']}},
            'status': {}}


class Boundaries(unittest.TestCase):
    def test_old_cluster_rejected(self):
        c = inputs(); c['cluster_id'] = L.OLD_CLUSTER
        with self.assertRaisesRegex(L.Stop, 'NEW_CLUSTER'):
            L.validate_inputs(c)

    def test_old_ingress_rejected(self):
        c = inputs(); c['ingress_domain'] = 'apps.seokpan-cloud.8zrv.p1.openshiftapps.com'
        with self.assertRaisesRegex(L.Stop, 'DELETED_INGRESS'):
            L.validate_inputs(c)

    def test_credential_field_rejected(self):
        c = inputs(); c['password'] = 'never-accepted'
        with self.assertRaisesRegex(L.Stop, 'EXACT_LAUNCH_INPUT'):
            L.validate_inputs(c)

    def test_unknown_protected_object_rejected(self):
        c = inputs(); c['protected_uids']['secret/other'] = 'x'
        with self.assertRaisesRegex(L.Stop, 'PROTECTED_OBJECT_UID'):
            L.validate_inputs(c)

    def test_budget_preserves_450_line_and_explicit_reserves(self):
        self.assertEqual(L.budget_check(inputs())['projected_total_usd'], '417.31')
        c = inputs(); c['budget']['hourly_upper_usd'] = 10
        with self.assertRaisesRegex(L.Stop, 'EXCEEDS_EXISTING_450'):
            L.budget_check(c)

    def test_missing_or_nan_budget_not_zero(self):
        for v in (None, 'NaN', True):
            c = inputs(); c['budget']['variable_allowance_usd'] = v
            with self.assertRaises(L.Stop):
                L.budget_check(c)

    def test_budget_window_requires_current_time(self):
        with self.assertRaisesRegex(L.Stop, 'CURRENT_EXECUTION_WINDOW'):
            L.budget_check(inputs(), dt.datetime(2031, 1, 1, tzinfo=dt.timezone.utc))

    def test_automated_empty_object_is_not_manual(self):
        a = app(); a['spec']['syncPolicy']['automated'] = {}
        with self.assertRaisesRegex(L.Stop, 'MANUAL_IDLE'):
            L.current_app(a)

    def test_extra_owners_and_conditions_block(self):
        for change in ('owner', 'condition'):
            a = app()
            if change == 'owner': a['metadata']['ownerReferences'] = [{'uid': 'foreign'}]
            else: a['status']['conditions'] = [{'type': 'ComparisonError'}]
            with self.assertRaises(L.Stop): L.current_app(a)

    def test_source_normalization_preserves_real_changes(self):
        a = {'kustomize': {'patches': [{'target': {'group': ''}, 'patch': '{"a":1}'}]}}
        b = {'kustomize': {'patches': [{'target': {}, 'patch': {'a': 1}}]}}
        self.assertEqual(L.normalize_source(a), L.normalize_source(b))
        b['kustomize']['patches'][0]['patch']['a'] = 2
        self.assertNotEqual(L.normalize_source(a), L.normalize_source(b))

    def test_measurement_cannot_use_ready_alone_or_changed_release(self):
        with patch.object(L, 'package', return_value=({}, inputs(), {}, {}, {})):
            with self.assertRaisesRegex(L.Stop, 'READY_RELEASE_AND_NORMAL_BUSINESS'):
                L.measurement(Path('.'), 'single', {'status': 'Synced'}, '')

    def test_data_handoff_before_any_api(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for name in ('handoff', 'sql', 'target'): L.write(td/(name+'.json'), {})
            e = dict(zip(('final_data_handoff', 'purpose_sql_receipt', 'purpose_sql_target'),
                         (str(td/(name+'.json')) for name in ('handoff', 'sql', 'target'))))
            with self.assertRaisesRegex(L.Stop, 'FINAL_DATA'):
                L.evidence(inputs(), e, 'single')


@unittest.skipUnless(os.environ.get('LAUNCH_PINNED_SOURCE') and os.environ.get('LAUNCH_KUSTOMIZE'), 'new inline render fixture not supplied')
class InlineRender(unittest.TestCase):
    def test_actual_inline_composition_and_release_record(self):
        # Keep child-process inputs in the explicitly writable workspace on Windows.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as td:
            out = Path(td)/'new'
            r = L.prepare(Path(os.environ['LAUNCH_PINNED_SOURCE']), out, inputs(), Path(os.environ['LAUNCH_KUSTOMIZE']))
            self.assertEqual([len(r['objects'][s]) for s in L.STAGES], [8, 8, 10])
            for stage in L.STAGES:
                p, c, src, desired, release = L.package(out, stage)
                self.assertEqual(src['targetRevision'], L.GITOPS)
                self.assertEqual(release['completeness'], 'COMPLETE')
                self.assertEqual(release['verification']['deployment'], 'NOT RUN')
                self.assertEqual(release['verification']['acceptance'], 'NOT RUN')
                text = json.dumps(desired)
                self.assertNotIn('input-required', text)
                self.assertNotIn('seokpan-cloud-ecr-pull', text)
                self.assertNotIn('Secret', [o['kind'] for o in desired['items']])
            if os.environ.get('LAUNCH_WRITE_FIXTURE'):
                shutil.copytree(out, Path(os.environ['LAUNCH_WRITE_FIXTURE']))
            altered = L.read(out/'single.source.json'); altered['targetRevision'] = 'main'
            (out/'single.source.json').write_text(json.dumps(altered))
            with self.assertRaisesRegex(L.Stop, 'PACKAGE_CONTENT_CHANGED'):
                L.package(out, 'single')


if __name__ == '__main__':
    unittest.main()
