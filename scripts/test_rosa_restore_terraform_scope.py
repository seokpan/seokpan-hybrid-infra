"""Run the real Terraform policy generator in an isolated, synthetic local Plan.

No Apply, backend, credentials, remote State or AWS request is used. The AWS
provider only formats aws_iam_policy_document data sources. TF_CLI_CONFIG_FILE
may select an existing provider mirror for offline execution.
"""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = '123456789012'
KEY = 'arn:aws:kms:ap-northeast-2:' + ACCOUNT + ':key/12345678-1234-1234-1234-123456789012'
SOURCES = {'snap-' + str(i) * 17: KEY for i in range(4)}
FIXTURE = '''
terraform {
  required_version = "= 1.16.4"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      version = "6.67.0"
    }
  }
}
provider "aws" {
  region = "ap-northeast-2"
  access_key = "fixture"
  secret_key = "fixture"
  skip_credentials_validation = true
  skip_requesting_account_id = true
  skip_metadata_api_check = true
  skip_region_validation = true
}
locals { account_id = "123456789012" }
resource "aws_iam_role" "tf" {
  for_each = { rosa = "fixture" }
  name = "fixture-never-applied"
  assume_role_policy = jsonencode({Version = "2012-10-17", Statement = []})
}
resource "aws_iam_role_policy" "tf_bootstrap" {
  name = "fixture-never-applied"
  role = aws_iam_role.tf["rosa"].id
  policy = jsonencode({Version = "2012-10-17", Statement = []})
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--terraform', default='terraform')
    parser.add_argument('--work-parent')
    parser.add_argument('--provider-lock-file', help='Existing lock with checksums for a local offline provider mirror; default is the committed Bootstrap lock.')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('restore_scope', ROOT / 'scripts/rosa-monitoring-restore-permission.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    scope = {'account_id': ACCOUNT, 'region': 'ap-northeast-2', 'kms_keys': [KEY], 'volumes': [
        {'snapshot_id': snapshot, 'source_kms_key': key, 'snapshot_state': 'completed', 'namespace': 'openshift-monitoring'}
        for snapshot, key in SOURCES.items()]}
    expected = helper.build_policy(scope)
    cases = []

    def altered(name, change):
        policy = copy.deepcopy(expected)
        change({s['Sid']: s for s in policy['Statement']}, policy)
        cases.append((name, dict(SOURCES), policy, False))

    cases.append(('structured-generator-only', dict(SOURCES), None, True))
    cases.append(('equivalent-legacy-assertion', dict(SOURCES), expected, True))
    reordered = copy.deepcopy(expected)
    reordered['Statement'].reverse()
    for statement in reordered['Statement']:
        statement['Action'].reverse()
        statement['Resource'].reverse()
    cases.append(('equivalent-ordering', dict(SOURCES), reordered, True))
    altered('action-wildcard', lambda by, p: by['UseExactSnapshots'].update(Action=['*']))
    altered('resource-wildcard', lambda by, p: by['UseExactSnapshots'].update(Resource=['*']))
    altered('extra-snapshot', lambda by, p: by['UseExactSnapshots']['Resource'].append('arn:aws:ec2:ap-northeast-2::snapshot/snap-' + '9' * 17))
    altered('extra-kms', lambda by, p: by['UseExactKeysThroughEc2']['Resource'].append(KEY + '-extra'))
    altered('missing-parent-snapshot', lambda by, p: by['CreateOnlyEncryptedSnapshotRestorations']['Condition'].pop('ArnEquals'))
    altered('missing-encryption', lambda by, p: by['CreateOnlyEncryptedSnapshotRestorations']['Condition'].pop('Bool'))
    altered('missing-via-service', lambda by, p: by['UseExactKeysThroughEc2'].pop('Condition'))
    altered('missing-grant-resource-condition', lambda by, p: by['ServiceResourceGrantsOnly']['Condition'].pop('Bool'))
    altered('extra-statement', lambda by, p: p['Statement'].append({'Sid': 'Extra', 'Effect': 'Allow', 'Action': ['iam:*'], 'Resource': ['*']}))
    altered('missing-create-tags-condition', lambda by, p: by['TagOnlyDuringVolumeCreation'].pop('Condition'))
    altered('missing-required-tags', lambda by, p: by['CreateOnlyEncryptedSnapshotRestorations']['Condition'].pop('Null'))
    cases.append(('five-structured-snapshots', dict(SOURCES, **{'snap-' + '9' * 17: KEY}), None, False))
    cases.append(('three-structured-snapshots', dict(list(SOURCES.items())[:3]), None, False))
    cases.append(('structured-wildcard-key', {s: '*' for s in SOURCES}, None, False))
    cases.append(('structured-foreign-account', {s: KEY.replace(ACCOUNT, '999999999999') for s in SOURCES}, None, False))
    cases.append(('structured-foreign-region', {s: KEY.replace('ap-northeast-2', 'us-east-1') for s in SOURCES}, None, False))

    env = {k: v for k, v in os.environ.items() if not k.startswith(('AWS_', 'TF_VAR_', 'TF_CLI_ARGS'))}
    env.update(AWS_EC2_METADATA_DISABLED='true', AWS_MAX_ATTEMPTS='1', CHECKPOINT_DISABLE='1',
               HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9', NO_PROXY='', TF_IN_AUTOMATION='1')
    # Init may download the exact provider from its registry in CI. It has no
    # backend or AWS credentials. Every subsequent Plan blocks HTTP(S) traffic.
    init_env = dict(env)
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY'):
        if name in os.environ:
            init_env[name] = os.environ[name]
        else:
            init_env.pop(name, None)
    with tempfile.TemporaryDirectory(prefix='tf-restore-boundary-', dir=args.work_parent) as folder:
        folder = Path(folder)
        env['TF_DATA_DIR'] = str(folder / '.terraform')
        init_env['TF_DATA_DIR'] = env['TF_DATA_DIR']
        (folder / 'main.tf').write_text(FIXTURE, encoding='utf-8')
        lock = Path(args.provider_lock_file) if args.provider_lock_file else ROOT / 'terraform/bootstrap/.terraform.lock.hcl'
        (folder / '.terraform.lock.hcl').write_bytes(lock.read_bytes())
        (folder / 'rosa_monitoring_restore.tf').write_bytes((ROOT / 'terraform/bootstrap/rosa_monitoring_restore.tf').read_bytes())

        def run(*command):
            selected_env = init_env if command[0] == 'init' else env
            return subprocess.run([args.terraform, '-chdir=' + str(folder), *command], env=selected_env,
                                  capture_output=True, text=True, encoding='utf-8', timeout=90)

        initialized = run('init', '-backend=false', '-input=false', '-lockfile=readonly', '-no-color')
        if initialized.returncode:
            raise RuntimeError('ISOLATED_PROVIDER_INIT_FAILED: ' + initialized.stderr[-1500:])
        results = []
        for name, sources, policy, accepted in cases:
            values = {'enable_rosa_monitoring_restore': True, 'rosa_monitoring_restore_sources': sources}
            if policy is not None:
                values['rosa_monitoring_restore_policy_json'] = json.dumps(policy)
            (folder / 'fixture.tfvars.json').write_text(json.dumps(values), encoding='utf-8')
            planned = run('plan', '-input=false', '-refresh=false', '-lock=false', '-no-color',
                          '-var-file=fixture.tfvars.json', '-out=fixture.tfplan')
            if (planned.returncode == 0) != accepted:
                raise AssertionError(name + ': ' + planned.stderr[-1800:])
            if not accepted:
                if not any(message in planned.stderr for message in (
                        'A supplied legacy policy must exactly match', 'Restoration requires exactly four',
                        'Restoration keys must belong')):
                    raise AssertionError('UNEXPECTED_FAILURE: ' + name + ': ' + planned.stderr[-1800:])
            else:
                shown = run('show', '-json', 'fixture.tfplan')
                if shown.returncode:
                    raise AssertionError('SYNTHETIC_PLAN_READ_FAILED')
                rows = json.loads(shown.stdout)['resource_changes']
                actual = next(r['change']['after']['policy'] for r in rows if r['address'] == helper.TARGET)
                if helper.doc(actual) != helper.doc(expected):
                    raise AssertionError('GENERATED_POLICY_DIFFERS_FROM_EXACT_PYTHON_SCOPE')
            results.append({'case': name, 'result': 'PASS', 'expected': 'ACCEPT' if accepted else 'REJECT'})
        print(json.dumps({'status': 'PASS_TERRAFORM_GENERATOR_AND_DIRECT_INPUT_BOUNDARIES', 'cases': results,
                          'terraform': '1.16.4', 'provider': '6.67.0', 'aws_api_calls': 0, 'apply_calls': 0,
                          'remote_state_access': False, 'fixture_only': True}, indent=2))


if __name__ == '__main__':
    main()
