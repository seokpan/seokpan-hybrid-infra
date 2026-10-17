"""Local-only current-input gate before dispatching existing create/restore entrypoints."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat

HERE = Path(__file__).resolve().parent
OUTPUT_KEYS = {'vpc_id', 'network_subnets', 'rosa_account_role_arns',
               'rosa_operator_policy_arns', 'rds_security_group_id', 'redis_security_group_id'}


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def read(path, private=True):
    p = Path(path)
    require(p.is_file() and not p.is_symlink(), 'REGULAR_LOCAL_INPUT_REQUIRED')
    require(p.stat().st_size <= 1048576, 'BOUNDED_INPUT_REQUIRED')
    if os.name == 'posix':
        s = p.stat()
        require(stat.S_ISREG(s.st_mode) and not (s.st_mode & 0o007), 'NON_PUBLIC_INPUT_REQUIRED')
        if private:
            require(s.st_uid == os.getuid() and not (s.st_mode & 0o077), 'PRIVATE_OWNED_INPUT_REQUIRED')
    raw = p.read_bytes()
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique), hashlib.sha256(raw).hexdigest()


def unwrap(value):
    if isinstance(value, dict) and 'value' in value and ('type' in value or 'sensitive' in value):
        return value['value']
    return value


def project_handoff(document):
    candidates = []
    def walk(value, depth=0):
        require(depth <= 16, 'HANDOFF_DEPTH_LIMIT')
        if isinstance(value, dict):
            if OUTPUT_KEYS <= set(value):
                candidates.append(value)
            for child in value.values():
                walk(child, depth+1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth+1)
    walk(document)
    require(len(candidates) == 1, 'UNIQUE_CURRENT_FOUNDATION_OUTPUTS_REQUIRED')
    o = {key: unwrap(candidates[0][key]) for key in OUTPUT_KEYS}
    return {'vpc_id': o['vpc_id'], 'subnets': {az: {key: row[key]
            for key in ('availability_zone', 'public_id', 'rosa_private_id')}
            for az, row in o['network_subnets'].items()},
            'account_roles': o['rosa_account_role_arns'],
            'operator_policy_arns': o['rosa_operator_policy_arns'],
            'data_security_groups': {'mariadb': o['rds_security_group_id'], 'redis': o['redis_security_group_id']}}


def project_inputs(foundation):
    return {key: foundation[key] for key in ('vpc_id', 'subnets', 'account_roles',
                                            'operator_policy_arns', 'data_security_groups')}


def differences(left, right, path='foundation'):
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        result = []
        for key in sorted(set(left) | set(right)):
            result += ([path+'.'+key] if key not in left or key not in right
                       else differences(left[key], right[key], path+'.'+key))
        return result
    if isinstance(left, list):
        return [] if left == right else [path]
    return [] if left == right else [path]


def template(c, input_sha):
    return {'schema': 1, 'source_cluster_id': c['source_cluster_id'],
            'recreate_inputs_sha256': input_sha,
            'foundation_handoff': {'file': '', 'sha256': '', 'revision': '', 'reference': ''},
            'network': {'ready': False, 'confirmed_at': None, 'valid_until': None, 'reference': '',
                        'nat_eip_disposition': 'UNKNOWN'},
            'budget': {'accepted': False, 'review_reference': '', 'window_reference': ''},
            'data': {'rds_state': 'unknown', 'reference': ''}}


def timestamp(value):
    require(isinstance(value, str), 'CURRENT_NETWORK_WINDOW_REQUIRED')
    value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(value.tzinfo is not None, 'TIMEZONE_REQUIRED')
    return value


def reference(value):
    return (isinstance(value, str) and len(value.strip()) >= 8
            and not value.upper().startswith(('PENDING', 'INPUT_REQUIRED')))


def evaluate(c, inputs, input_sha, proof, handoff, now, not_before, pending_create):
    missing = []
    require(c.get('schema') == 1 and c.get('scope') == 'ROSA_ONLY', 'ROSA_ONLY_CONFIG_REQUIRED')
    require(proof.get('schema') == 1 and proof.get('source_cluster_id') == c['source_cluster_id'],
            'CURRENT_SOURCE_CLUSTER_BINDING_REQUIRED')
    action = 'restore' if c['current_cluster_id'] != c['source_cluster_id'] else 'create'
    if now.astimezone(dt.timezone(dt.timedelta(hours=9))).date() < not_before:
        missing.append('EXECUTION_DATE_NOT_REACHED')
    if action == 'create' and pending_create:
        return {'status': 'STOP_RECREATE_READINESS', 'reason': 'PRIOR_CREATE_APPLY_REQUIRES_RECONCILIATION',
                'action': action, 'authentication_started': False, 'resource_mutations': 0}
    require(proof.get('recreate_inputs_sha256') == input_sha, 'REVIEWED_CURRENT_INPUT_HASH_REQUIRED')
    require(inputs.get('cluster_enabled') is True and inputs.get('worker_sg_binding') is None,
            'NEW_CLUSTER_UNBOUND_WORKER_INPUT_REQUIRED')
    for key in ('inputs_accepted', 'held_restoration_contract_accepted'):
        if c['create'].get(key) is not True:
            missing.append('CONFIG_'+key.upper())
    net, budget = proof.get('network', {}), proof.get('budget', {})
    for label, value in [('CURRENT_NETWORK_HANDOFF', net.get('ready') is True),
                         ('CONFIG_NETWORK_READY', c['create'].get('network_ready') is True)]:
        if not value:
            missing.append(label)
    if not reference(net.get('reference')):
        missing.append('CURRENT_NETWORK_REFERENCE')
    try:
        start, end = timestamp(net.get('confirmed_at')), timestamp(net.get('valid_until'))
        if not start <= now <= end:
            missing.append('CURRENT_NETWORK_WINDOW')
    except (RuntimeError, ValueError, TypeError):
        missing.append('CURRENT_NETWORK_WINDOW')
    if action == 'create':
        if c['create'].get('budget_accepted') is not True or budget.get('accepted') is not True:
            missing.append('CURRENT_BUDGET_ACCEPTANCE')
        reviews = inputs.get('execution_review', {})
        for key, input_key in [('review_reference', 'cost_review'), ('window_reference', 'window')]:
            if not reference(budget.get(key)) or budget[key] != reviews.get(input_key):
                missing.append('CURRENT_BUDGET_'+key.upper())
    f = proof.get('foundation_handoff', {})
    if handoff is None or not reference(f.get('reference')):
        missing.append('CURRENT_FOUNDATION_HANDOFF')
    else:
        diff = differences(project_inputs(inputs['foundation']), project_handoff(handoff))
        if diff:
            return {'status': 'STOP_RECREATE_READINESS', 'reason': 'CURRENT_FOUNDATION_INPUT_DIFFERS',
                    'difference_paths': diff, 'authentication_started': False, 'resource_mutations': 0}
        if (not f.get('revision') or f['revision'] != inputs['foundation'].get('revision')
                or inputs.get('execution_review', {}).get('foundation_revision') != f['revision']):
            missing.append('CURRENT_FOUNDATION_REVISION_BINDING')
    rds_state = proof.get('data', {}).get('rds_state', 'unknown')
    require(rds_state in ('unknown', 'available', 'stopped', 'stopping', 'starting'), 'RDS_STATE_LABEL_REQUIRED')
    return {'status': 'PENDING_RECREATE_INPUTS' if missing else 'PASS_LOCAL_RECREATE_DISPATCH_READY_NOT_EXECUTED',
            'missing': sorted(set(missing)), 'action': action,
            'rds_state_reported': rds_state,
            'rds_queried_or_started': False, 'cloud_sql_or_app_allowed': False,
            'network_readiness': 'OWNER_HANDOFF_ONLY_NOT_LIVE_PROBED',
            'authentication_started': False, 'resource_mutations': 0,
            'config_or_approval_bits_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--readiness', required=True)
    parser.add_argument('--not-before', required=True)
    parser.add_argument('--out', type=Path, required=True)
    a = parser.parse_args()
    os.umask(0o077)
    try:
        c, _ = read(a.config)
        inputs, input_sha = read(c['create']['var_file'])
        readiness = Path(a.readiness)
        created = not readiness.exists()
        if created:
            with readiness.open('x', encoding='utf-8') as stream:
                json.dump(template(c, input_sha), stream, indent=2)
            readiness.chmod(0o600)
        proof, _ = read(readiness)
        f = proof.get('foundation_handoff', {})
        handoff = None
        if f.get('file'):
            handoff, sha = read(f['file'], private=False)
            require(sha == f.get('sha256'), 'CURRENT_FOUNDATION_FILE_HASH_REQUIRED')
        original, _ = read(c['delete']['manifest'])
        old_inputs, _ = read(original['var_file'])
        diff = differences(project_inputs(old_inputs['foundation']), project_inputs(inputs['foundation']))
        require(not diff, 'FOUNDATION_RESOURCE_IDENTITIES_CHANGED_REQUIRES_NEW_SCOPE_REVIEW')
        pending = list(Path(c['evidence_root']).glob('rosa-create-*/apply-started.NO_AUTOMATIC_RETRY.json'))
        report = evaluate(c, inputs, input_sha, proof, handoff, dt.datetime.now(dt.timezone.utc),
                          dt.date.fromisoformat(a.not_before), bool(pending))
        report['readiness_template_created'] = created
        if report['status'] == 'PASS_LOCAL_RECREATE_DISPATCH_READY_NOT_EXECUTED':
            with (a.out/'dispatch.json').open('x', encoding='utf-8') as stream:
                json.dump({'action': report['action'], 'config': str(Path(a.config).resolve())}, stream)
    except Exception as error:
        report = {'status': 'STOP_RECREATE_READINESS',
                  'reason': str(error) if type(error) is RuntimeError else type(error).__name__,
                  'authentication_started': False, 'resource_mutations': 0, 'automatic_retry': False}
    (a.out/'readiness-receipt.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    print('Private readiness result directory: '+str(a.out))
    return 0 if report['status'] == 'PASS_LOCAL_RECREATE_DISPATCH_READY_NOT_EXECUTED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
