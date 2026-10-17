"""Bind an explicit deletion approval; inspect residual resources without deleting them."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def load_lifecycle():
    spec = importlib.util.spec_from_file_location('approved_delete_entry', HERE/'rosa-lifecycle-entry.py')
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    return entry


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
    Path(path).chmod(0o600)


def prepare(a):
    entry = load_lifecycle()
    L = entry.L
    c = L.read_json(a.config)
    m = L.read_json(c['delete']['manifest'])
    review_dir = Path(a.review_dir)
    review = L.read_json(review_dir/'receipt.json')
    require(a.accept_preservation_impact and a.independent_decrypt_accepted,
            'EXPLICIT_CUTOFF_IMPACT_AND_EXTERNAL_DECRYPT_ACCEPTANCE_REQUIRED')
    require(len(a.approval_reference) >= 8 and not any(x in a.approval_reference for x in '\r\n\0'),
            'EXPLICIT_USER_APPROVAL_REFERENCE_REQUIRED')
    require(c.get('scope') == 'ROSA_ONLY' and m.get('action') == 'delete'
            and c.get('current_cluster_id') == c.get('source_cluster_id') == m.get('cluster_id') == a.cluster_id,
            'EXACT_APPROVED_ROSA_CLUSTER_REQUIRED')
    require(m.get('plan_sha256') == a.plan_sha256 and L.sha(m['plan']) == a.plan_sha256,
            'EXACT_APPROVED_SAVED_PLAN_REQUIRED')
    require(not (Path(c['delete']['manifest']).parent/'apply-started.NO_AUTOMATIC_RETRY.json').exists(),
            'PREVIOUS_APPLY_REQUIRES_RECONCILIATION_NOT_RETRY')
    require(review.get('status') == 'PASS_NEW_STATE_AND_RESTART_SUPPLEMENT_LOCAL_ROUNDTRIP'
            and review.get('archive_sha256') == a.review_archive_sha256
            and L.sha(review_dir/'delete-review.sops.json') == a.review_archive_sha256,
            'ACCEPTED_FINAL_REVIEW_ARCHIVE_REQUIRED')
    require(review.get('held_restoration_contract_accepted') is True
            and review.get('recreate_inputs_accepted') is True
            and c['create'].get('inputs_accepted') is True
            and c['create'].get('held_restoration_contract_accepted') is True
            and c['restoration'].get('new_volume_permissions_accepted') is True,
            'ACCEPTED_RESTART_INPUTS_REQUIRED')
    state = L.read_json(Path(c['delete']['manifest']).parent/'before.tfstate')
    handoff = state['outputs']['platform_handoff']['value']
    require(handoff.get('cluster_id') == a.cluster_id
            and re.fullmatch(r'[a-zA-Z0-9-]+', handoff.get('infra_id', '')),
            'SAVED_CLUSTER_OWNERSHIP_REQUIRED')
    approval = dict(action='delete', cluster_id=a.cluster_id, plan_sha256=a.plan_sha256,
                    approved_by_user=True, approval_reference=a.approval_reference,
                    approved_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                    accepted_snapshot_cutoffs_utc=review['snapshot_cutoffs_utc'],
                    accepted_ephemeral_impact=review['impact'])
    approval.update({k: True for k in L.approval_gates('delete')})
    approval_file = a.out/'explicit-user-delete-approval.json'
    save(approval_file, approval)
    # Keep the archived original config and future network/budget gates unchanged.
    c['delete']['approval_file'] = str(approval_file)
    config_file = a.out/'approved-delete-config.json'
    save(config_file, c)
    _, paths = entry.settings(str(config_file), 'delete', HERE.parent)
    save(a.out/'paths.json', dict(paths, config=str(config_file)))
    return {'status': 'PREPARED_EXPLICIT_PLAN_BOUND_DELETE_APPROVAL_NOT_APPLIED',
            'deletion_started': False, 'original_config_changed': False,
            'network_budget_gates_changed': False}


def command(args, errors, label, runner=subprocess.run):
    try:
        result = runner(['aws', *args, '--region', 'ap-northeast-2', '--output', 'json',
                         '--cli-connect-timeout', '10', '--cli-read-timeout', '30'],
                        capture_output=True, text=True, timeout=180)
        if result.returncode:
            match = re.search(r'\(([^()\s]+)\) when calling', result.stderr)
            errors.append({'operation': label, 'reason': match.group(1) if match else 'READ_FAILED'})
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError) as error:
        errors.append({'operation': label, 'reason': type(error).__name__})
        return None


def inventory(a):
    L = load_lifecycle().L
    c = L.read_json(a.config)
    m = L.read_json(c['delete']['manifest'])
    old = L.read_json(Path(c['delete']['manifest']).parent/'before.tfstate')
    handoff = old['outputs']['platform_handoff']['value']
    require(handoff.get('cluster_id') == m['cluster_id'] == c['source_cluster_id'], 'EXPECTED_OLD_CLUSTER_REQUIRED')
    infra = handoff['infra_id']
    require(re.fullmatch(r'[a-zA-Z0-9-]+', infra), 'EXPECTED_INFRA_ID_REQUIRED')
    errors = []
    caller = command(['sts', 'get-caller-identity'], errors, 'sts:GetCallerIdentity')
    require(caller and caller.get('Account') == m['account_id']
            and ':assumed-role/seokpan-tf-rosa/' in caller.get('Arn', ''), 'EXPECTED_ACCOUNT_PURPOSE_ROLE_REQUIRED')
    tag = 'kubernetes.io/cluster/' + infra
    rows = {}
    for service, operation, key in [('ec2', 'describe-instances', 'Reservations'),
                                     ('ec2', 'describe-volumes', 'Volumes'),
                                     ('ec2', 'describe-network-interfaces', 'NetworkInterfaces')]:
        data = command([service, operation, '--filters', 'Name=tag:'+tag+',Values=owned'],
                       errors, service+':'+operation)
        rows[key] = data.get(key, []) if data is not None else None
    tagged = command(['resourcegroupstaggingapi', 'get-resources', '--tag-filters',
                      json.dumps([{'Key': tag, 'Values': ['owned']}])], errors, 'tag:GetResources')
    resources = tagged.get('ResourceTagMappingList', []) if tagged is not None else None
    instances = None if rows['Reservations'] is None else [
        {'id': i['InstanceId'], 'state': i['State']['Name']}
        for r in rows['Reservations'] for i in r.get('Instances', []) if i['State']['Name'] != 'terminated']
    volumes = None if rows['Volumes'] is None else [
        {'id': v['VolumeId'], 'state': v['State'], 'size_gib': v['Size']} for v in rows['Volumes']]
    lbs = None if resources is None else [r['ResourceARN'] for r in resources
                                          if ':elasticloadbalancing:' in r['ResourceARN']]
    save(a.out/'owned-resource-inventory.private.json', {'instances': instances, 'volumes': volumes,
         'network_interfaces': rows['NetworkInterfaces'], 'tagged_resources': resources})
    journal = L.read_json(Path(c['restoration']['snapshot_map']).parent/'snapshot-journal.json')
    ids = [v['snapshot_id'] for v in journal['volumes']]
    require(len(ids) == len(set(ids)) == 4 and journal['cluster_id'] == m['cluster_id']
            and all(re.fullmatch(r'snap-[a-f0-9]+', value) for value in ids), 'EXACT_RETAINED_SNAPSHOT_IDS_REQUIRED')
    snap = command(['ec2', 'describe-snapshots', '--snapshot-ids', *ids], errors, 'ec2:DescribeSnapshots')
    snapshots = None if snap is None else [{'id': s['SnapshotId'], 'state': s['State']} for s in snap['Snapshots']]
    return {'status': 'PARTIAL_POST_DELETE_INVENTORY' if errors else 'COLLECTED_POST_DELETE_INVENTORY',
            'delete_entry_exit_code': a.delete_exit_code,
            'remaining_owned_instances': instances, 'remaining_owned_ebs_volumes': volumes,
            'remaining_owned_load_balancers': None if lbs is None else len(lbs),
            'remaining_owned_network_interfaces': None if rows['NetworkInterfaces'] is None else len(rows['NetworkInterfaces']),
            'retained_monitoring_snapshots': snapshots, 'read_errors': errors,
            'NAT_EIP_common_foundation_data': 'RETAINED_EXCLUDED_SEPARATE_OWNER',
            'snapshot_storage_cost': 'CONTINUES', 'actual_cost_amount': 'NOT_QUERIED',
            'billing_zero_confirmed': False,
            'coverage': 'EXACT_OLD_INFRA_OWNED_TAG_ONLY_UNTAGGED_OR_SHARED_RESOURCES_NOT_PROVEN_ABSENT',
            'resource_mutations': 0, 'automatic_delete_retry': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'inventory'])
    parser.add_argument('--config', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--review-dir')
    parser.add_argument('--review-archive-sha256')
    parser.add_argument('--cluster-id')
    parser.add_argument('--plan-sha256')
    parser.add_argument('--approval-reference', default='')
    parser.add_argument('--accept-preservation-impact', action='store_true')
    parser.add_argument('--independent-decrypt-accepted', action='store_true')
    parser.add_argument('--delete-exit-code', type=int, default=-1)
    a = parser.parse_args()
    os.umask(0o077)
    try:
        result = prepare(a) if a.mode == 'prepare' else inventory(a)
    except Exception as error:
        result = {'status': 'STOP_APPROVED_DELETE_'+a.mode.upper(),
                  'reason': str(error) if type(error) is RuntimeError else type(error).__name__,
                  'automatic_retry': False, 'deletion_success_asserted': False}
    save(a.out/(a.mode+'-receipt.json'), result)
    print(json.dumps(result, indent=2))
    print('Private approval/inventory directory: '+str(a.out))
    return 1 if result['status'].startswith('STOP_') else 0


if __name__ == '__main__':
    raise SystemExit(main())
