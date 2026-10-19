"""New structured Terraform-input wiring; all execution helpers are synthetic."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import test_rosa_monitoring_restore_permission as T
import test_rosa_monitoring_restore_reconcile as C

R = T.R


class StructuredInputs(unittest.TestCase):
    def test_same_four_protected_sources_are_sorted_and_complete(self):
        scope = T.scope()
        expected = {row['snapshot_id']: row['source_kms_key'] for row in scope['volumes']}
        self.assertEqual(R.terraform_sources(scope), expected)
        self.assertEqual(list(R.terraform_sources(scope)), sorted(expected))

    def test_invalid_scope_cannot_reach_terraform_variable_generation(self):
        scope = T.scope()
        scope['volumes'][0]['snapshot_state'] = 'pending'
        with self.assertRaises(RuntimeError):
            R.terraform_sources(scope)

    def test_snapshot_shape_matches_the_terraform_generator(self):
        scope = T.scope()
        scope['volumes'][0]['snapshot_id'] = 'snap-a'
        with self.assertRaisesRegex(RuntimeError, 'COMPLETED_MONITORING_SNAPSHOTS_ONLY'):
            R.terraform_sources(scope)

    def test_existing_conflicting_map_is_rejected_without_overwrite(self):
        values = {'rosa_monitoring_restore_sources': {'snap-foreign': '*'}}
        before = copy.deepcopy(values)
        with self.assertRaisesRegex(RuntimeError, 'STRUCTURED_RESTORE_SCOPE_CHANGED'):
            R.bind_terraform_sources(values, T.scope())
        self.assertEqual(values, before)

    def test_initial_synthetic_apply_receives_structured_scope_and_legacy_assertion(self):
        with tempfile.TemporaryDirectory() as folder:
            result, run, _ = T.RestorePermissions().apply_fixture(folder)
            self.assertEqual(result['status'], 'PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED')
            values = json.loads((Path(folder) / 'out/restore-scope.tfvars.json').read_text())
            self.assertEqual(values['rosa_monitoring_restore_sources'], R.terraform_sources(T.scope()))
            self.assertEqual(R.doc(values['rosa_monitoring_restore_policy_json']), R.doc(R.build_policy(T.scope())))
            self.assertEqual(run.applies, 1)

    def test_synthetic_partial_reconciliation_binds_new_file_preserving_old_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            result, run, _ = C.PartialRestore().fixture(folder)
            self.assertEqual(result['status'], 'PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED')
            before = json.loads((Path(folder) / 'partial/restore-scope.tfvars.json').read_text())
            after = json.loads((Path(folder) / 'out/remainder.tfvars.json').read_text())
            self.assertNotIn('rosa_monitoring_restore_sources', before)
            self.assertEqual(after['rosa_monitoring_restore_sources'], R.terraform_sources(T.scope()))
            self.assertEqual(run.applies, 1)


if __name__ == '__main__':
    unittest.main()
