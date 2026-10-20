"""Only new managed-provider boundaries; no real OAuth, Secret or API access."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('managed', Path(__file__).with_name('repair_managed_github_idp.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class ManagedGitHub(unittest.TestCase):
    def setUp(self):
        self.provider = {'name': 'seokpan-github', 'type': 'GitHub', 'mappingMethod': 'claim',
                         'github': {'clientID': M.CLIENT, 'organizations': ['seokpan'],
                                    'clientSecret': {'name': 'accepted-secret'}}}
        self.desired = M.desired_provider(self.provider, 'only-synthetic-secret')

    def test_absent_provider_selects_one_create_preserving_admin(self):
        items = [{'name': 'cluster-admin', 'id': 'existing', 'type': 'HTPasswdIdentityProvider'}]
        previous = copy.deepcopy(items)
        self.assertEqual(M.decision(items, self.desired), ('create', None))
        self.assertEqual(items, previous)

    def test_matching_existing_provider_is_reused(self):
        current = dict(self.desired, id='provider-id')
        current['github'] = dict(current['github'])
        current['github'].pop('client_secret')
        self.assertEqual(M.decision([current], self.desired)[0], 'reuse')

    def test_wrong_client_or_scope_cannot_be_overwritten(self):
        for key, value in (('client_id', 'different-client'), ('organizations', ['different'])):
            current = copy.deepcopy(self.desired)
            current['id'] = 'provider-id'
            current['github'][key] = value
            with self.assertRaisesRegex(RuntimeError, 'NO_OVERWRITE'):
                M.decision([current], self.desired)

    def test_login_disabled_selects_only_login_enable(self):
        current = dict(self.desired, id='provider-id', login=False)
        self.assertEqual(M.decision([current], self.desired)[0], 'enable_login')

    def test_unrestricted_or_changed_client_definition_rejected(self):
        for provider in (dict(self.provider, github={'clientID': M.CLIENT}),
                         dict(self.provider, github={'clientID': 'wrong', 'organizations': ['seokpan']})):
            with self.assertRaises(RuntimeError):
                M.desired_provider(provider, 'only-synthetic-secret')

    def test_generated_oauth_secret_reference_does_not_change_identity_scope(self):
        rendered = copy.deepcopy(self.provider)
        rendered['github']['clientSecret']['name'] = 'new-managed-generated-secret'
        self.assertEqual(M.public_provider(rendered), dict(M.public_provider(self.desired), type='GitHub'))


if __name__ == '__main__':
    unittest.main()
