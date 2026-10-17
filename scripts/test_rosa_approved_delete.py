import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('approved', Path(__file__).with_name('rosa-approved-delete.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class ApprovedDeleteBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name)
        self.out = p/'out'
        self.out.mkdir()
        review = p/'review'
        review.mkdir()
        self.plan = p/'saved.tfplan'
        self.plan.write_bytes(b'saved-plan-fixture')
        archive = review/'delete-review.sops.json'
        archive.write_bytes(b'encrypted-archive-fixture')
        self.config = p/'config.json'
        manifest = p/'plan-manifest.json'
        self.write(manifest, {'action': 'delete', 'cluster_id': 'cluster-fixture',
                             'plan': str(self.plan), 'plan_sha256': D.load_lifecycle().L.sha(self.plan)})
        self.write(p/'before.tfstate', {'outputs': {'platform_handoff': {'value': {
            'cluster_id': 'cluster-fixture', 'infra_id': 'fixture-infra'}}}})
        self.write(review/'receipt.json', {'status': 'PASS_NEW_STATE_AND_RESTART_SUPPLEMENT_LOCAL_ROUNDTRIP',
            'archive_sha256': D.load_lifecycle().L.sha(archive), 'held_restoration_contract_accepted': True,
            'recreate_inputs_accepted': True, 'snapshot_cutoffs_utc': ['fixture-cutoff'], 'impact': {'pods': []}})
        self.write(self.config, {'scope': 'ROSA_ONLY', 'source_cluster_id': 'cluster-fixture',
            'current_cluster_id': 'cluster-fixture', 'delete': {'manifest': str(manifest), 'approval_file': 'old-false'},
            'create': {'inputs_accepted': True, 'held_restoration_contract_accepted': True,
                       'network_ready': False, 'budget_accepted': False},
            'restoration': {'new_volume_permissions_accepted': True}})
        self.original = self.config.read_bytes()
        self.args = SimpleNamespace(config=str(self.config), review_dir=str(review),
            accept_preservation_impact=True, independent_decrypt_accepted=True,
            approval_reference='fixture-explicit-user-approval', cluster_id='cluster-fixture',
            plan_sha256=D.load_lifecycle().L.sha(self.plan),
            review_archive_sha256=D.load_lifecycle().L.sha(archive), out=self.out)
        entry = D.load_lifecycle()
        entry.settings = lambda *args: ({}, {'rosa_cli': 'fixture-rosa', 'tf_session_helper': 'fixture-helper'})
        self.patcher = patch.object(D, 'load_lifecycle', return_value=entry)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')
        path.chmod(0o600)

    def test_user_acknowledgment_required(self):
        self.args.accept_preservation_impact = False
        with self.assertRaisesRegex(RuntimeError, 'EXPLICIT_CUTOFF'):
            D.prepare(self.args)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_exact_cluster_required(self):
        self.args.cluster_id = 'different'
        with self.assertRaisesRegex(RuntimeError, 'EXACT_APPROVED_ROSA_CLUSTER'):
            D.prepare(self.args)

    def test_changed_plan_is_rejected(self):
        self.plan.write_bytes(b'changed-plan')
        with self.assertRaisesRegex(RuntimeError, 'EXACT_APPROVED_SAVED_PLAN'):
            D.prepare(self.args)

    def test_prior_apply_never_replayed(self):
        (self.plan.parent/'apply-started.NO_AUTOMATIC_RETRY.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'PREVIOUS_APPLY'):
            D.prepare(self.args)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_approved_copy_preserves_original_and_future_gates(self):
        result = D.prepare(self.args)
        self.assertFalse(result['deletion_started'])
        self.assertEqual(self.config.read_bytes(), self.original)
        new = json.loads((self.out/'approved-delete-config.json').read_text())
        self.assertFalse(new['create']['network_ready'])
        self.assertFalse(new['create']['budget_accepted'])
        approval = json.loads((self.out/'explicit-user-delete-approval.json').read_text())
        self.assertTrue(approval['approved_by_user'])
        self.assertEqual(approval['plan_sha256'], self.args.plan_sha256)
        self.assertEqual(approval['accepted_snapshot_cutoffs_utc'], ['fixture-cutoff'])

    def test_read_denial_is_unknown_not_empty(self):
        errors = []
        fake = lambda *a, **kw: SimpleNamespace(returncode=255, stderr='An error occurred (AccessDenied) when calling', stdout='')
        result = D.command(['ec2', 'describe-volumes'], errors, 'volumes', runner=fake)
        self.assertIsNone(result)
        self.assertEqual(errors, [{'operation': 'volumes', 'reason': 'AccessDenied'}])

    def test_timeout_is_reported_without_retry(self):
        errors = []
        calls = []
        def fake(*args, **kwargs):
            calls.append(args)
            raise subprocess.TimeoutExpired('fixture', 180)
        self.assertIsNone(D.command(['ec2', 'describe-instances'], errors, 'instances', runner=fake))
        self.assertEqual(len(calls), 1)
        self.assertEqual(errors[0]['reason'], 'TimeoutExpired')


if __name__ == '__main__':
    unittest.main()
