"""Check the declared ROSA/Foundation tag ownership boundary; no Cloud access."""
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'terraform/foundation'


def blocks(text, pattern):
    out = []
    for match in re.finditer(pattern, text, re.M):
        start = match.end() - 1
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    out.append((match.groups(), text[start + 1:i]))
                    break
        else:
            raise AssertionError('Unclosed source block')
    return out


class TagOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {p.name: p.read_text(encoding='utf-8-sig') for p in ROOT.glob('*.tf')}
        cls.providers = [body for _, body in blocks(cls.sources['providers.tf'], r'^provider "aws"\s*\{')]
        cls.default = next(b for b in cls.providers if not re.search(r'\balias\s*=', b))
        cls.shared = next(b for b in cls.providers if re.search(r'alias\s*=\s*"rosa_shared_tags"', b))
        cls.resources = {}
        for text in cls.sources.values():
            cls.resources.update({kind + '.' + name: body for (kind, name), body in
                                  blocks(text, r'^resource "([^"]+)" "([^"]+)"\s*\{')})

    def test_default_provider_keeps_all_tag_management(self):
        self.assertEqual(len(self.providers), 2)
        self.assertNotIn('ignore_tags', self.default)

    def test_shared_provider_ignores_only_cluster_prefix(self):
        found = blocks(self.shared, r'\bignore_tags\s*\{')
        self.assertEqual(len(found), 1)
        self.assertRegex(found[0][1], r'^\s*key_prefixes\s*=\s*\["kubernetes\.io/cluster/"\]\s*$')
        self.assertNotIn('ignore_changes', '\n'.join(self.sources.values()))

    def test_same_region_and_identical_default_tags(self):
        for p in (self.default, self.shared):
            self.assertRegex(p, r'region\s*=\s*var\.aws_region')
        a = blocks(self.default, r'\bdefault_tags\s*\{')[0][1]
        b = blocks(self.shared, r'\bdefault_tags\s*\{')[0][1]
        self.assertEqual(re.sub(r'\s+', '', a), re.sub(r'\s+', '', b))
        for key in ('Project', 'Phase', 'ManagedBy', 'Component'):
            self.assertIn(key, a)

    def test_alias_has_no_distinct_account_or_credentials(self):
        for token in ('assume_role', 'access_key', 'secret_key', 'profile', 'endpoints', 'skip_'):
            self.assertNotIn(token, self.shared)

    def test_alias_is_limited_to_rosa_shared_resources(self):
        selected = {key for key, b in self.resources.items()
                    if re.search(r'\bprovider\s*=\s*aws\.rosa_shared_tags\b', b)}
        self.assertEqual(selected, {'aws_vpc.main', 'aws_subnet.public',
                                    'aws_subnet.rosa', 'aws_iam_role.rosa_account'})

    def test_data_exclusion_stays_explicitly_managed(self):
        b = self.resources['aws_subnet.data']
        self.assertNotRegex(b, r'\bprovider\s*=')
        self.assertRegex(b, r'"kubernetes\.io/cluster/unmanaged"\s*=\s*"true"')
        self.assertIn('for_each = var.network_az_ids', b)

    def test_elb_role_tags_are_not_ignored(self):
        self.assertRegex(self.resources['aws_subnet.public'], r'"kubernetes\.io/role/elb"\s*=\s*"1"')
        self.assertRegex(self.resources['aws_subnet.rosa'], r'"kubernetes\.io/role/internal-elb"\s*=\s*"1"')

    def test_role_trust_and_attachment_contract_remain(self):
        b = self.resources['aws_iam_role.rosa_account']
        self.assertIn('for_each = local.rosa_account_trust_policies', b)
        self.assertRegex(b, r'assume_role_policy\s*=\s*each\.value')
        self.assertRegex(b, r'permissions_boundary\s*=\s*var\.rosa_account_permissions_boundary')
        a = self.resources['aws_iam_role_policy_attachment.rosa_account']
        self.assertRegex(a, r'policy_arn\s*=\s*aws_iam_policy\.rosa_account\[each.key\]\.arn')


if __name__ == '__main__':
    unittest.main()
