"""Offline review aids. No APIs, Terraform commands, credentials, or file writes.
Results are self-consistency/structural checks, never execution approval.
Python 3.9+; diagnostics contain fixed codes and counts only.
"""
import argparse
import hashlib
import math
import json
import os
from pathlib import Path
import re
import stat
import sys

class Invalid(Exception):
    pass

def pairs(rows):
    obj = {}
    for key, value in rows:
        if key in obj:
            raise Invalid('DUPLICATE_JSON_KEY')
        obj[key] = value
    return obj

def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise Invalid('NONFINITE_JSON')
    return value

def validate_file_stat(st, posix=None, uid=None):
    if not stat.S_ISREG(st.st_mode):
        raise Invalid('FILE_TYPE_OR_SIZE')
    if posix is None: posix = os.name == 'posix'
    if posix:
        if uid is None: uid = os.getuid()
        if st.st_uid != uid or stat.S_IMODE(st.st_mode) != 0o600:
            raise Invalid('FILE_OWNER_OR_MODE')

def read_json(path, limit=64 * 1024 * 1024):
    path = Path(path)
    before = path.lstat()
    validate_file_stat(before)
    if before.st_size > limit:
        raise Invalid('FILE_TYPE_OR_SIZE')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        validate_file_stat(opened)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise Invalid('INPUT_FILE_CHANGED_DURING_OPEN')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise Invalid('FILE_TYPE_OR_SIZE')
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                           parse_float=finite_float,
                           parse_constant=lambda _: (_ for _ in ()).throw(Invalid('NONFINITE_JSON')))
    except (UnicodeError, ValueError):
        raise Invalid('JSON_FORMAT')
    if not isinstance(value, dict):
        raise Invalid('JSON_OBJECT_REQUIRED')
    return value

SG_CONTRACT = {'mariadb': ('rds_security_group_id', 'seokpan-fnd-rds'),
               'redis': ('redis_security_group_id', 'seokpan-fnd-redis')}

def sg_review(inputs, outputs, observations):
    errors = set()
    foundation = inputs.get('foundation')
    if not isinstance(foundation, dict):
        raise Invalid('FOUNDATION_INPUT_MISSING')
    account, vpc = foundation.get('account_id'), foundation.get('vpc_id')
    mapping = foundation.get('data_security_groups')
    if not isinstance(account, str) or not re.fullmatch(r'[0-9]{12}', account):
        errors.add('PROJECT_ACCOUNT_ID_FORMAT')
    if not isinstance(vpc, str) or not re.fullmatch(r'vpc-(?:[0-9a-f]{8}|[0-9a-f]{17})', vpc):
        errors.add('VPC_ID_FORMAT')
    if set(outputs) != {v[0] for v in SG_CONTRACT.values()}:
        errors.add('LIMITED_SG_OUTPUT_KEYS_REQUIRED')
    if not isinstance(mapping, dict) or set(mapping) != set(SG_CONTRACT):
        raise Invalid('DATA_SG_KEYS_REQUIRED')
    groups = observations.get('SecurityGroups')
    if not isinstance(groups, list) or len(groups) != 2 or any(not isinstance(x, dict) for x in groups):
        raise Invalid('TWO_SG_OBSERVATIONS_REQUIRED')
    by_id = {g.get('GroupId'): g for g in groups if isinstance(g.get('GroupId'), str)}
    if len(by_id) != 2:
        errors.add('DUPLICATE_OR_MISSING_OBSERVED_SG')
    ids = list(mapping.values())
    if any(not isinstance(x, str) or not re.fullmatch(r'sg-(?:[0-9a-f]{8}|[0-9a-f]{17})', x) for x in ids):
        errors.add('SG_ID_FORMAT')
    elif len(set(ids)) != 2:
        errors.add('DATA_SG_IDS_NOT_DISTINCT')
    for key, (output_name, group_name) in SG_CONTRACT.items():
        supplied = outputs.get(output_name)
        # Accept a limited Terraform output entry or its extracted string.
        if isinstance(supplied, dict):
            if supplied.get('sensitive') is not False:
                errors.add('OUTPUT_SENSITIVITY_UNCONFIRMED')
            supplied = supplied.get('value')
        if supplied != mapping[key] or not isinstance(supplied, str):
            errors.add('OUTPUT_TO_INPUT_MAPPING_MISMATCH')
        group = by_id.get(mapping[key]) if isinstance(mapping[key], str) else None
        if not group:
            errors.add('SUPPLIED_SG_NOT_OBSERVED')
            continue
        if group.get('GroupName') != group_name:
            errors.add('SG_PURPOSE_NAME_MISMATCH')
        if group.get('VpcId') != vpc:
            errors.add('SG_VPC_MISMATCH')
        if group.get('OwnerId') != account:
            errors.add('SG_PROJECT_ACCOUNT_MISMATCH')
        tags = group.get('Tags')
        if not isinstance(tags, list) or any(not isinstance(t, dict) for t in tags):
            errors.add('SG_TAG_FORMAT')
        else:
            components = [t.get('Value') for t in tags if t.get('Key') == 'Component']
            if components != ['data']:
                errors.add('SG_COMPONENT_TAG_MISMATCH')
    return {'scope': 'offline_sg_semantic_consistency',
            'result': 'BLOCKED' if errors else 'CONSISTENT_PENDING_HANDOFF_REVIEW',
            'reason_codes': sorted(errors), 'sg_count': len(groups),
            'not_verified': ['A/C source and receipt authenticity/current revision',
                             'live AWS existence and current account/caller',
                             'ingress/egress rules, Worker SG ownership, permissions']}

BASE = {'terraform_data.input_contract': 1, 'rhcs_rosa_oidc_config.cluster': 1,
        'aws_iam_openid_connect_provider.cluster': 1, 'aws_iam_role.operator': 6,
        'aws_iam_role_policy_attachment.operator': 6}
DATA_ADDRESSES = re.compile(r'data\.(?:aws_caller_identity\.current|aws_vpc\.foundation|rhcs_rosa_operator_roles\.cluster|aws_iam_role\.account\["(?:installer|support|controlplane|worker)"\]|aws_subnet\.(?:public|rosa_private)\["az_[abc]"\])\Z')

def managed_base(row):
    address = row.get('address')
    kind, name = row.get('type'), row.get('name')
    if not all(isinstance(x, str) for x in (address, kind, name)):
        return None
    base = kind + '.' + name
    if base in ('aws_iam_role.operator', 'aws_iam_role_policy_attachment.operator'):
        match = re.fullmatch(re.escape(base) + r'\["([^"\r\n]+)"\]', address)
        return base if match and row.get('index') == match.group(1) else None
    if base == 'rhcs_cluster_rosa_classic.cluster':
        return base if address == base + '[0]' and type(row.get('index')) is int and row['index'] == 0 else None
    return base if address == base and base in BASE else None

def plan_review(plan, stage):
    if stage not in ('iam-oidc-preparation', 'cluster-create'):
        raise Invalid('UNSUPPORTED_REVIEW_STAGE')
    errors, pending = set(), set()
    expected = dict(BASE)
    expected['rhcs_cluster_rosa_classic.cluster'] = 1 if stage == 'cluster-create' else 0
    counts = {x: 0 for x in expected}
    actions_count = {'create': 0, 'no-op': 0, 'update': 0, 'delete': 0, 'replace': 0, 'other': 0}
    if not isinstance(plan.get('format_version'), str) or not re.fullmatch(r'1\.[0-9]+', plan['format_version']):
        errors.add('UNSUPPORTED_JSON_FORMAT')
    if plan.get('terraform_version') != '1.16.4':
        errors.add('CORE_VERSION_MISMATCH')
    for flag, wanted in [('complete', True), ('errored', False), ('applyable', True)]:
        if plan.get(flag) is not wanted:
            errors.add('PLAN_' + flag.upper() + '_NOT_ACCEPTABLE')
    variables = plan.get('variables', {})
    if not isinstance(variables, dict):
        raise Invalid('PLAN_VARIABLES_FORMAT')
    if variables.get('cluster_enabled', {}).get('value') is not (stage == 'cluster-create'):
        errors.add('STAGE_CLUSTER_FLAG_MISMATCH')
    if variables.get('worker_sg_binding', {}).get('value', 'MISSING') is not None:
        errors.add('FIRST_STAGE_BINDING_MUST_BE_NULL')
    changes = plan.get('resource_changes')
    if not isinstance(changes, list):
        raise Invalid('RESOURCE_CHANGES_MISSING')
    seen, role_keys, attachment_keys, observed_data = set(), set(), set(), set()
    for row in changes:
        if not isinstance(row, dict) or not isinstance(row.get('change'), dict):
            raise Invalid('RESOURCE_CHANGE_FORMAT')
        address = row.get('address')
        if not isinstance(address, str):
            raise Invalid('RESOURCE_ADDRESS_FORMAT')
        if address in seen:
            errors.add('DUPLICATE_RESOURCE_ADDRESS')
        seen.add(address)
        if row.get('module_address') or row.get('previous_address') or row.get('deposed') or row['change'].get('importing') or row.get('generated_config') or row['change'].get('generated_config'):
            errors.add('MODULE_MOVE_IMPORT_OR_DEPOSED_REVIEW')
        actions = row['change'].get('actions')
        if not isinstance(actions, list) or not actions or any(not isinstance(x, str) for x in actions):
            raise Invalid('RESOURCE_ACTION_FORMAT')
        if 'delete' in actions:
            errors.add('DELETION_PRESENT')
            if 'create' in actions:
                errors.add('REPLACEMENT_PRESENT')
        if 'update' in actions:
            errors.add('UPDATE_REQUIRES_SEPARATE_REVIEW')
        if row.get('mode') == 'data':
            observed_data.add(address)
            if not DATA_ADDRESSES.fullmatch(address) or actions not in (['read'], ['no-op']):
                errors.add('UNEXPECTED_DATA_RESOURCE_OR_ACTION')
            if address.split('.')[1:3] != [row.get('type'), row.get('name')]:
                # indexed name is compared separately below
                base_address = address.split('[', 1)[0]
                if base_address != 'data.' + str(row.get('type')) + '.' + str(row.get('name')):
                    errors.add('DATA_ADDRESS_METADATA_MISMATCH')
            continue
        if row.get('mode') != 'managed':
            errors.add('RESOURCE_MODE_UNKNOWN')
        base = managed_base(row)
        if base not in expected:
            errors.add('RESOURCE_OUTSIDE_FIRST_ROSA_SCOPE')
        else:
            counts[base] += 1
            if base == 'aws_iam_role.operator': role_keys.add(row['index'])
            if base == 'aws_iam_role_policy_attachment.operator': attachment_keys.add(row['index'])
        category = ('replace' if actions in (['delete', 'create'], ['create', 'delete']) else
                    actions[0] if len(actions) == 1 and actions[0] in actions_count else 'other')
        actions_count[category] += 1
        if actions not in (['create'], ['no-op']):
            errors.add('FIRST_PLAN_ACTION_REQUIRES_REVIEW')
    expected_data = {'data.aws_caller_identity.current', 'data.aws_vpc.foundation', 'data.rhcs_rosa_operator_roles.cluster'}
    expected_data |= {'data.aws_iam_role.account[' + json.dumps(k) + ']' for k in ('installer', 'support', 'controlplane', 'worker')}
    expected_data |= {'data.aws_subnet.' + kind + '[' + json.dumps(k) + ']' for kind in ('public', 'rosa_private') for k in ('az_a', 'az_b', 'az_c')}
    if observed_data != expected_data:
        pending.add('DATA_READ_COMPLETENESS_REVIEW')
    if counts != expected:
        errors.add('EXPECTED_RESOURCE_COUNTS_MISMATCH')
    if role_keys != attachment_keys:
        errors.add('OPERATOR_ROLE_ATTACHMENT_KEY_MISMATCH')
    prior = plan.get('prior_state')
    if prior is not None and not isinstance(prior, dict):
        raise Invalid('PRIOR_STATE_FORMAT')
    if prior and prior.get('values'):
        pending.add('EXISTING_STATE_OWNERSHIP_REVIEW')
    if actions_count['no-op']:
        pending.add('EXISTING_MANAGED_RESOURCES_REVIEW')
    if plan.get('resource_drift'):
        errors.add('DRIFT_REQUIRES_REVIEW')
    if plan.get('deferred_changes'):
        errors.add('DEFERRED_CHANGES_PRESENT')
    checks = plan.get('checks')
    if not isinstance(checks, list) or not checks:
        pending.add('CHECK_RESULTS_NOT_AVAILABLE')
    else:
        for item in checks:
            if not isinstance(item, dict) or item.get('status') not in ('pass', 'fail', 'error', 'unknown'):
                errors.add('CHECK_RESULT_FORMAT')
            elif item['status'] in ('fail', 'error'):
                errors.add('FAILED_CHECK_PRESENT')
            elif item['status'] == 'unknown':
                pending.add('CHECKS_PENDING_APPLY')
    return {'scope': 'offline_first_rosa_plan_structure', 'stage': stage,
            'result': 'BLOCKED' if errors else 'NO_LISTED_ANOMALY_PENDING_REVIEW',
            'reason_codes': sorted(errors), 'pending_codes': sorted(pending),
            'managed_resource_counts': counts, 'managed_action_counts': actions_count,
            'data_scope': 'OBSERVED_ROWS_ONLY_ACTUAL_READ_SUCCESS_NOT_VERIFIED',
            'observed_data_address_count': len(observed_data), 'expected_data_address_count': len(expected_data),
            'not_verified': ['execution Source/Lock/input hashes and full-root command receipt',
                             'source configuration equivalence, current State and real resource ownership',
                             'IAM policy/trust/attribute values, support, quotas, cost, execution window',
                             'actual service-internal machine/disk quantities, Apply approval']}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    sg = sub.add_parser('sg')
    for name in ('inputs', 'outputs', 'observations'): sg.add_argument('--' + name, required=True)
    p = sub.add_parser('plan')
    p.add_argument('--plan', required=True)
    p.add_argument('--stage', choices=['iam-oidc-preparation', 'cluster-create'], required=True)
    args = parser.parse_args()
    try:
        result = sg_review(read_json(args.inputs), read_json(args.outputs), read_json(args.observations)) if args.mode == 'sg' else plan_review(read_json(args.plan), args.stage)
        result['local_file_permissions'] = 'OWNER_600_CHECKED' if os.name == 'posix' else 'WINDOWS_ACL_NOT_CHECKED'
        result['reference_source_head'] = 'aaa8cff09cd70298b142afdba5faf15d73664cc3'
        result['live_execution_source_match'] = 'NOT_VERIFIED'
        print(json.dumps(result, sort_keys=True))
        return 2 if result['result'] == 'BLOCKED' else 0
    except Invalid as exc:
        print(json.dumps({'result': 'BLOCKED', 'reason_codes': [str(exc)]}))
        return 2
    except Exception:
        print(json.dumps({'result': 'BLOCKED', 'reason_codes': ['LOCAL_INPUT_OR_STRUCTURE_ERROR']}))
        return 2

if __name__ == '__main__':
    sys.exit(main())
