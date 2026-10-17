import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('readiness', Path(__file__).with_name('rosa-recreate-readiness.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


class RecreateReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 19, 1, tzinfo=dt.timezone.utc)
        self.day = dt.date(2026, 10, 19)
        self.c = {'schema': 1, 'scope': 'ROSA_ONLY', 'source_cluster_id': 'old-fixture',
                  'current_cluster_id': 'old-fixture', 'create': {'inputs_accepted': True,
                   'held_restoration_contract_accepted': True, 'network_ready': True, 'budget_accepted': True}}
        f = {'vpc_id': 'vpc-fixture', 'revision': 'foundation-revision-fixture',
             'subnets': {az: {'availability_zone': az, 'public_id': 'pub-'+az,
                             'rosa_private_id': 'rosa-'+az} for az in ('az_a', 'az_b', 'az_c')},
             'account_roles': {'worker': 'worker-fixture'}, 'operator_policy_arns': {'p': 'policy-fixture'},
             'data_security_groups': {'mariadb': 'db-fixture', 'redis': 'redis-fixture'}}
        self.v = {'foundation': f, 'cluster_enabled': True, 'worker_sg_binding': None,
                  'execution_review': {'foundation_revision': f['revision'],
                                      'cost_review': 'cost-review-fixture', 'window': 'window-fixture'}}
        self.a = {'vpc_id': f['vpc_id'], 'network_subnets': f['subnets'],
                  'rosa_account_role_arns': f['account_roles'], 'rosa_operator_policy_arns': f['operator_policy_arns'],
                  'rds_security_group_id': 'db-fixture', 'redis_security_group_id': 'redis-fixture'}
        self.p = R.template(self.c, 'sha-fixture')
        self.p['foundation_handoff'].update(revision=f['revision'], reference='foundation-proof-fixture')
        self.p['network'].update(ready=True, confirmed_at='2026-10-19T00:00:00Z',
                                 valid_until='2026-10-19T05:00:00Z', reference='network-proof-fixture')
        self.p['budget'].update(accepted=True, review_reference='cost-review-fixture', window_reference='window-fixture')

    def run_gate(self, **kw):
        return R.evaluate(kw.get('c', self.c), self.v, 'sha-fixture', self.p,
                          kw.get('handoff', self.a), kw.get('now', self.now), self.day,
                          kw.get('pending', False))

    def test_ready_dispatch_is_only_a_local_gate(self):
        before = copy.deepcopy((self.c, self.v, self.p))
        r = self.run_gate()
        self.assertEqual(r['status'], 'PASS_LOCAL_RECREATE_DISPATCH_READY_NOT_EXECUTED')
        self.assertEqual(r['action'], 'create')
        self.assertFalse(r['authentication_started'])
        self.assertEqual(before, (self.c, self.v, self.p))

    def test_removed_nat_does_not_authenticate_or_dispatch(self):
        self.p['network']['ready'] = False
        self.c['create']['network_ready'] = False
        r = self.run_gate()
        self.assertEqual(r['status'], 'PENDING_RECREATE_INPUTS')
        self.assertIn('CURRENT_NETWORK_HANDOFF', r['missing'])
        self.assertFalse(r['authentication_started'])

    def test_expired_network_attestation_blocks(self):
        self.p['network']['valid_until'] = '2026-10-19T00:30:00Z'
        self.assertIn('CURRENT_NETWORK_WINDOW', self.run_gate()['missing'])

    def test_changed_subnet_reports_paths_not_values(self):
        self.a['network_subnets'] = copy.deepcopy(self.a['network_subnets'])
        self.a['network_subnets']['az_a']['rosa_private_id'] = 'changed-private-input'
        r = self.run_gate()
        self.assertEqual(r['reason'], 'CURRENT_FOUNDATION_INPUT_DIFFERS')
        self.assertEqual(r['difference_paths'], ['foundation.subnets.az_a.rosa_private_id'])
        self.assertNotIn('changed-private-input', str(r))

    def test_stopped_rds_allows_platform_only(self):
        self.p['data']['rds_state'] = 'stopped'
        r = self.run_gate()
        self.assertEqual(r['status'], 'PASS_LOCAL_RECREATE_DISPATCH_READY_NOT_EXECUTED')
        self.assertFalse(r['rds_queried_or_started'])
        self.assertFalse(r['cloud_sql_or_app_allowed'])

    def test_pending_existing_create_is_not_replayed(self):
        r = self.run_gate(pending=True)
        self.assertEqual(r['reason'], 'PRIOR_CREATE_APPLY_REQUIRES_RECONCILIATION')

    def test_existing_new_cluster_dispatches_restore_only(self):
        self.c['current_cluster_id'] = 'new-fixture'
        r = self.run_gate(pending=True)
        self.assertEqual(r['action'], 'restore')
        self.assertEqual(r['status'], 'PASS_LOCAL_RECREATE_DISPATCH_READY_NOT_EXECUTED')

    def test_changed_input_hash_requires_review(self):
        self.p['recreate_inputs_sha256'] = 'different'
        with self.assertRaisesRegex(RuntimeError, 'REVIEWED_CURRENT_INPUT_HASH'):
            self.run_gate()

    def test_unreviewed_budget_does_not_create(self):
        self.p['budget']['accepted'] = False
        self.assertIn('CURRENT_BUDGET_ACCEPTANCE', self.run_gate()['missing'])

    def test_sunday_does_not_create(self):
        r = self.run_gate(now=dt.datetime(2026, 10, 18, 0, tzinfo=dt.timezone.utc))
        self.assertIn('EXECUTION_DATE_NOT_REACHED', r['missing'])

    def test_source_handoff_accepts_only_one_projection(self):
        with self.assertRaisesRegex(RuntimeError, 'UNIQUE_CURRENT_FOUNDATION'):
            R.project_handoff({'a': self.a, 'b': self.a})

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'DUPLICATE_JSON_KEY'):
            R.unique([('ready', False), ('ready', True)])


if __name__ == '__main__':
    unittest.main()
