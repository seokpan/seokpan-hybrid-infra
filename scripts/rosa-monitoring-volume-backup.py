"""Exact four-monitoring-volume EBS snapshot capture, with no ROSA deletion.

Encrypted source-to-snapshot/AZ/KMS mappings are retained for reconstruction.
Writes are explicit, dry-run checked for all four volumes before the first write,
and journaled before each non-idempotent API call. Unknown outcomes are not retried.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('checkpoint', HERE/'prepare-rosa-checkpoint.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
need = B.need
REGION = 'ap-northeast-2'


def save(path, value):
    path.write_bytes(B.canonical(value))
    path.chmod(0o600)


def exclusive(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(B.canonical(value))


def restoration_review(gap):
    """Object identities/pinned versions only, never Secret data or client IDs."""
    inventory = gap.get('restoration_inventory', [])
    need(len(inventory) <= 200, 'BOUNDED_RESTORATION_INVENTORY_REQUIRED')
    return {
        'unclassified_object_ids': [r['id'] for r in inventory if r['action'] == 'REVIEW_BEFORE_REPLAY'],
        'protected_secret_ids': [r['id'] for r in inventory if r['action'] == 'PROTECTED_RESUPPLY_REVIEW'],
        'operator_versions': [{'name': o['metadata']['name'], 'version': o.get('spec', {}).get('version')}
                              for o in gap.get('snapshots', []) if o.get('kind') == 'ClusterServiceVersion']}


def aws(argv, out, allow_dry_run=False):
    env = os.environ.copy()
    for key in ('AWS_PROFILE', 'AWS_DEFAULT_PROFILE', 'AWS_ROLE_ARN', 'AWS_WEB_IDENTITY_TOKEN_FILE'):
        env.pop(key, None)
    env.update(AWS_MAX_ATTEMPTS='1', AWS_PAGER='')
    command = ['aws', '--region', REGION, '--output', 'json',
               '--cli-connect-timeout', '10', '--cli-read-timeout', '45', *argv]
    try:
        p = subprocess.run(command, capture_output=True, env=env, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError('AWS_OUTCOME_UNKNOWN_NO_AUTOMATIC_RETRY')
    if allow_dry_run:
        if not (p.returncode != 0 and b'DryRunOperation' in p.stderr):
            (out/'aws-failure-private.log').write_bytes(p.stdout+p.stderr)
            raise RuntimeError('SNAPSHOT_DRY_RUN_DENIED_NO_PERMISSION_CHANGE')
        return {}
    if p.returncode:
        (out/'aws-failure-private.log').write_bytes(p.stdout+p.stderr)
        error = re.search(rb'An error occurred \(([A-Za-z0-9.]+)\)', p.stderr)
        code = error[1].decode() if error else 'UNCLASSIFIED'
        raise RuntimeError('AWS_'+code+'_NO_AUTOMATIC_RETRY')
    return json.loads(p.stdout)


def validate_sources(rows, observed, infra):
    need(len(rows) == 4 and len({r['volume_id'] for r in rows}) == 4,
         'EXACT_FOUR_VOLUME_PLAN_REQUIRED')
    need(all(r['namespace'] == 'openshift-monitoring' for r in rows), 'MONITORING_NAMESPACE_ONLY')
    by_id = {v['VolumeId']: v for v in observed.get('Volumes', [])}
    need(set(by_id) == {r['volume_id'] for r in rows}, 'VOLUME_SET_CHANGED')
    result = []
    for row in rows:
        v = by_id[row['volume_id']]
        tags = {t['Key']: t['Value'] for t in v.get('Tags', [])}
        need(tags.get('kubernetes.io/cluster/'+infra) in ('owned', 'shared'), 'VOLUME_CLUSTER_OWNERSHIP_REQUIRED')
        need(tags.get('CSIVolumeName') == 'pvc-'+row['claim_uid'], 'VOLUME_PVC_UID_TAG_REQUIRED')
        need(v.get('Encrypted') is True and v.get('KmsKeyId'), 'ENCRYPTED_SOURCE_VOLUME_REQUIRED')
        need(re.fullmatch(REGION+'[a-z]', v.get('AvailabilityZone', '')), 'VOLUME_REGION_REQUIRED')
        need(v.get('State') == 'in-use', 'EXPECTED_CURRENT_MONITORING_VOLUME_REQUIRED')
        size = re.fullmatch(r'([0-9]+)Gi', row['capacity']['storage'])
        need(size and v['Size'] == int(size[1]), 'ACCEPTED_PVC_CAPACITY_CHANGED')
        result.append({**row, 'source_az': v['AvailabilityZone'], 'source_kms_key': v['KmsKeyId'],
                       'size_gib': v['Size'], 'snapshot_id': None, 'snapshot_state': 'NOT_CREATED'})
    return result


def restore_volumes(a, manifest, out, binary, audit):
    """Explicit future reconstruction; called only after a different new ROSA is ready."""
    need(a.snapshot_map and a.snapshot_map_sha256 and a.new_handoff and a.kubeconfig,
         'EXPLICIT_SNAPSHOT_MAP_NEW_HANDOFF_AND_CONTEXT_REQUIRED')
    raw = B.private_file(a.snapshot_map, B.MAX_BYTES*3)
    need(hashlib.sha256(raw).hexdigest() == a.snapshot_map_sha256, 'SNAPSHOT_MAP_HASH_REQUIRED')
    bundle = json.loads(B.crypt(binary, raw, decrypt=True, key_file=a.key_file))
    need(bundle.get('purpose') == 'ROSA_MONITORING_SNAPSHOT_MAP' and bundle.get('volume_data_backed_up') is True,
         'COMPLETED_SNAPSHOT_MAP_REQUIRED')
    journal = bundle['volume_preservation']
    need(journal['cluster_id'] == manifest['cluster_id'] and journal['account_id'] == manifest['account_id'],
         'ORIGINAL_CLUSTER_SNAPSHOT_SCOPE_REQUIRED')
    handoff = json.loads(B.private_file(a.new_handoff, 1048576))
    new_id = handoff.get('cluster_id', '')
    need(re.fullmatch(r'[a-z0-9]{10,64}', new_id) and new_id != manifest['cluster_id']
         and handoff.get('state') == 'ready', 'DIFFERENT_READY_NEW_ROSA_REQUIRED')
    previous_state = json.loads(B.private_file(Path(a.delete_manifest).parent/'before.tfstate'))
    old_api = previous_state.get('outputs', {}).get('platform_handoff', {}).get('value', {}).get('api_url')
    need(old_api and handoff.get('api_url') != old_api
         and re.fullmatch(r'https://api\.[a-z0-9.-]+\.openshiftapps\.com:6443', handoff.get('api_url', '')),
         'DIFFERENT_ACCEPTED_NEW_API_REQUIRED')
    B.private_file(a.kubeconfig, 262144)
    oc = ['oc', '--kubeconfig', a.kubeconfig, '--request-timeout=20s']
    def kube(args, payload=None, empty=False):
        p = subprocess.run(oc+args, input=payload, capture_output=True, timeout=40)
        need(p.returncode == 0, 'NEW_CLUSTER_VOLUME_API_FAILED_NO_RETRY')
        if args[0] == 'create':
            return None
        return json.loads(p.stdout) if p.stdout.strip() else None
    trust = kube(['config', 'view', '--minify', '-o', 'json'])['clusters'][0]['cluster']
    need(trust.get('server', '').rstrip('/') == handoff.get('api_url', '').rstrip('/')
         and not trust.get('insecure-skip-tls-verify'), 'NEW_API_CONTEXT_TRUST_REQUIRED')
    nodes = kube(['get', 'nodes', '-o', 'json'])['items']
    zones = {n['metadata'].get('labels', {}).get('topology.kubernetes.io/zone') for n in nodes}
    rows = journal['volumes']
    need(len(rows) == 4 and len({r['claim'] for r in rows}) == 4
         and all(r['snapshot_state'] == 'completed' and r['source_az'] in zones for r in rows),
         'FOUR_COMPLETED_SNAPSHOTS_AND_NEW_ZONE_CAPACITY_REQUIRED')
    checked = aws(['ec2', 'describe-snapshots', '--owner-ids', journal['account_id'], '--snapshot-ids',
                   *[r['snapshot_id'] for r in rows]], out)
    validate_snapshots(rows, checked, journal['account_id'], journal['run'])
    planned = []
    for row in rows:
        name = 'rosa-restored-'+hashlib.sha256((new_id+row['claim']).encode()).hexdigest()[:20]
        for kind, object_name, namespace in [('pv', name, None), ('pvc', row['claim'], row['namespace'])]:
            args = ['get', kind, object_name, '--ignore-not-found=true', '-o', 'json']
            if namespace:
                args += ['-n', namespace]
            need(kube(args) is None, 'EXISTING_PV_OR_PVC_BLOCKS_RECONSTRUCTION_NO_TAKEOVER')
        declarations = restore_declarations(row, 'vol-00000000000000000', name)
        kube(['create', '--dry-run=server', '-f', '-', '-o', 'json'],
             B.canonical({'apiVersion': 'v1', 'kind': 'List', 'items': list(declarations)}))
        token = hashlib.sha256((new_id+row['claim']+row['snapshot_id']).encode()).hexdigest()
        args = ['ec2', 'create-volume', '--snapshot-id', row['snapshot_id'],
                '--availability-zone', row['source_az'], '--volume-type', 'gp3',
                '--size', str(row['size_gib']), '--encrypted', '--kms-key-id', row['source_kms_key'],
                '--client-token', token, '--tag-specifications', json.dumps([{'ResourceType': 'volume', 'Tags':[
                    {'Key': 'seokpan-restored-cluster', 'Value': new_id},
                    {'Key': 'seokpan-source-snapshot', 'Value': row['snapshot_id']}]}])]
        aws(args+['--dry-run'], out, allow_dry_run=True)
        planned.append((row, name, args))
    marker = Path(a.output_parent)/('monitoring-volume-restore-'+new_id+'.NO_AUTOMATIC_RETRY.json')
    exclusive(marker, {'cluster_id': new_id, 'result_directory': str(out)})
    created_rows = []
    save(out/'restore-journal.json', {'new_cluster_id': new_id, 'volumes': created_rows})
    for row, name, args in planned:
        audit['aws_volume_create_attempts'] = audit.get('aws_volume_create_attempts', 0)+1
        created = aws(args, out)
        need(re.fullmatch(r'vol-[0-9a-f]+', created.get('VolumeId', '')), 'NEW_VOLUME_OUTCOME_UNKNOWN_NO_RETRY')
        created_rows.append({**row, 'new_volume_id': created['VolumeId'], 'new_pv_name': name})
        save(out/'restore-journal.json', {'new_cluster_id': new_id, 'volumes': created_rows})
    deadline = time.monotonic()+600
    while True:
        values = aws(['ec2', 'describe-volumes', '--volume-ids', *[r['new_volume_id'] for r in created_rows]], out)['Volumes']
        by_id = {v['VolumeId']: v for v in values}
        need(set(by_id) == {r['new_volume_id'] for r in created_rows}, 'NEW_VOLUME_SET_CHANGED')
        for row in created_rows:
            v = by_id[row['new_volume_id']]
            need(v.get('Encrypted') is True and v.get('SnapshotId') == row['snapshot_id']
                 and v.get('KmsKeyId') == row['source_kms_key'] and v.get('AvailabilityZone') == row['source_az'],
                 'NEW_VOLUME_ENCRYPTION_OR_SOURCE_CHANGED')
        if all(v.get('State') == 'available' for v in values):
            break
        need(time.monotonic() < deadline, 'NEW_VOLUMES_PENDING_RECONCILE_NO_RECREATE')
        time.sleep(10)
    objects = [o for row in created_rows for o in restore_declarations(row, row['new_volume_id'], row['new_pv_name'])]
    audit.update(kubernetes_create_attempted=True, cluster_mutations=None,
                 kubernetes_create_outcome_unknown=True)
    kube(['create', '-f', '-', '-o', 'json'], B.canonical({'apiVersion': 'v1', 'kind': 'List', 'items': objects}))
    audit.update(cluster_mutations=8, kubernetes_create_outcome_unknown=False)
    deadline = time.monotonic()+180
    while True:
        bound = 0
        for row in created_rows:
            claim = kube(['get', 'pvc', row['claim'], '-n', row['namespace'], '-o', 'json'])
            pv = kube(['get', 'pv', row['new_pv_name'], '-o', 'json'])
            need(claim['spec'].get('volumeName') == row['new_pv_name']
                 and pv['spec']['csi']['volumeHandle'] == row['new_volume_id'], 'NEW_PVC_BINDING_TARGET_CHANGED')
            if claim.get('status', {}).get('phase') == 'Bound':
                need(pv['spec']['claimRef'].get('uid') == claim['metadata']['uid'], 'NEW_PVC_UID_NOT_BOUND')
                bound += 1
        if bound == 4:
            break
        need(time.monotonic() < deadline, 'PVS_PVCS_PENDING_RECONCILE_NO_RECREATE')
        time.sleep(10)
    return {'status': 'PASS_NEW_MONITORING_VOLUMES_AND_PVC_BOUND_NOT_TSDB_RUNTIME',
            'cluster_id': new_id, 'volumes_created': 4, 'pvcs_bound': 4,
            'monitoring_configuration': 'APPLY_AFTER_THIS_STAGE', 'prometheus_alertmanager_runtime': 'NOT_TESTED',
            'application_started': False}


def snapshot_args(row, run):
    tags = [{'Key': 'seokpan-preservation-run', 'Value': run},
            {'Key': 'seokpan-preservation-scope', 'Value': 'rosa-monitoring'},
            {'Key': 'seokpan-source-pvc-uid', 'Value': row['claim_uid']}]
    return ['ec2', 'create-snapshot', '--volume-id', row['volume_id'],
            '--description', 'ROSA monitoring checkpoint '+run,
            '--tag-specifications', json.dumps([{'ResourceType': 'snapshot', 'Tags': tags}])]


def preflight_snapshot_writes(rows, run, out):
    # Finish the whole set of permission checks before any non-idempotent call.
    for row in rows:
        aws(snapshot_args(row, run)+['--dry-run'], out, allow_dry_run=True)


def validate_snapshots(rows, observed, account, run):
    by_id = {s['SnapshotId']: s for s in observed.get('Snapshots', [])}
    need(set(by_id) == {r['snapshot_id'] for r in rows}, 'EXACT_SNAPSHOT_SET_REQUIRED')
    for row in rows:
        s = by_id[row['snapshot_id']]
        tags = {t['Key']: t['Value'] for t in s.get('Tags', [])}
        need(s.get('OwnerId') == account and s.get('VolumeId') == row['volume_id']
             and s.get('Encrypted') is True and s.get('KmsKeyId') == row['source_kms_key']
             and s.get('VolumeSize') == row['size_gib']
             and tags.get('seokpan-preservation-run') == run
             and tags.get('seokpan-source-pvc-uid') == row['claim_uid'], 'SNAPSHOT_IDENTITY_OR_ENCRYPTION_CHANGED')
        need(s.get('State') in ('pending', 'completed'), 'SNAPSHOT_FAILED_PRESERVE_AND_RECONCILE')
        row.update(snapshot_state=s['State'], snapshot_start_time=s.get('StartTime'))
    return all(r['snapshot_state'] == 'completed' for r in rows)


def restore_declarations(row, new_volume_id, new_pv_name):
    """No replay of the old PV/claim UID; bind a new volume before monitoring config."""
    need(re.fullmatch(r'vol-[0-9a-f]+', new_volume_id), 'NEW_EBS_VOLUME_ID_REQUIRED')
    need(new_volume_id != row['volume_id'], 'OLD_ROSA_VOLUME_REPLAY_FORBIDDEN')
    need(re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,62}', new_pv_name), 'NEW_PV_NAME_REQUIRED')
    need(row['filesystem'] in ('ext4', 'xfs') and row['volume_mode'] == 'Filesystem'
         and row['access_modes'] == ['ReadWriteOnce'] and row['storage_class'] == 'gp3', 'ADOPTED_FILESYSTEM_AND_PVC_CONTRACT_REQUIRED')
    terms = row['node_affinity']['required']['nodeSelectorTerms']
    need(terms and all(term.get('matchExpressions') and not term.get('matchFields')
        and all(e.get('key') in ('topology.ebs.csi.aws.com/zone', 'topology.kubernetes.io/zone',
                                'topology.kubernetes.io/region') for e in term['matchExpressions'])
        for term in terms), 'STABLE_ZONE_AFFINITY_REQUIRED_NO_OLD_NODE_REPLAY')
    pv = {'apiVersion': 'v1', 'kind': 'PersistentVolume', 'metadata': {'name': new_pv_name},
        'spec': {'capacity': {'storage': str(row['size_gib'])+'Gi'}, 'accessModes': row['access_modes'],
            'volumeMode': row['volume_mode'], 'storageClassName': row['storage_class'], 'persistentVolumeReclaimPolicy': 'Retain',
            'claimRef': {'namespace': row['namespace'], 'name': row['claim']},
            'csi': {'driver': 'ebs.csi.aws.com', 'fsType': row['filesystem'], 'volumeHandle': new_volume_id},
            'nodeAffinity': row['node_affinity']}}
    pvc = {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
        'metadata': {'name': row['claim'], 'namespace': row['namespace']},
        'spec': {'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem', 'storageClassName': 'gp3',
            'volumeName': new_pv_name, 'resources': {'requests': {'storage': str(row['size_gib'])+'Gi'}}}}
    return pv, pvc


def finish(a, journal, out, binary):
    rows = journal['volumes']
    need(len(rows) == 4 and all(re.fullmatch(r'snap-[0-9a-f]+', r.get('snapshot_id') or '') for r in rows),
         'KNOWN_FOUR_SNAPSHOT_IDS_REQUIRED_NO_CREATE_RETRY')
    deadline = time.monotonic()+a.wait_seconds
    while True:
        snapshots = aws(['ec2', 'describe-snapshots', '--owner-ids', journal['account_id'],
                         '--snapshot-ids', *[r['snapshot_id'] for r in rows]], out)
        complete = validate_snapshots(rows, snapshots, journal['account_id'], journal['run'])
        save(out/'snapshot-journal.json', journal)
        if complete:
            break
        if time.monotonic() >= deadline:
            return {'status': 'PENDING_FOUR_SNAPSHOTS_COMPLETION_READ_ONLY_RESUME',
                    'snapshots_created': 4, 'snapshots_completed': sum(r['snapshot_state'] == 'completed' for r in rows),
                    'resume_journal': str(out/'snapshot-journal.json')}
        time.sleep(min(15, max(0, deadline-time.monotonic())))
    contract = json.loads(B.private_file(a.contract, 262144))
    source = B.private_file(contract['recipient_source'], 262144)
    need(hashlib.sha256(source).hexdigest() == contract['recipient_source_sha256'], 'RECIPIENT_SOURCE_HASH_MISMATCH')
    recipients, _ = B.age_recipients(source)
    recipient = B.key_matched_recipient(recipients, a, out)
    bundle = {'schema': 1, 'origin_host': __import__('socket').gethostname(),
        'purpose': 'ROSA_MONITORING_SNAPSHOT_MAP', 'snapshots': [], 'files': [],
        'volume_preservation': journal, 'volume_data_backed_up': True,
        'consistency': 'CRASH_CONSISTENT_PER_VOLUME_NO_WRITER_QUIESCE',
        'restoration_runtime': 'NOT_TESTED', 'deletion_ready': False}
    raw = B.canonical(bundle)
    encrypted = B.crypt(binary, raw, recipient=recipient)
    archive = out/'monitoring-snapshots.sops.json'
    archive.write_bytes(encrypted)
    need(B.canonical(json.loads(B.crypt(binary, encrypted, decrypt=True, key_file=a.key_file))) == raw,
         'SNAPSHOT_MAP_LOCAL_ROUNDTRIP_FAILED')
    return {'status': 'PASS_FOUR_ENCRYPTED_EBS_SNAPSHOTS_AND_RESTORE_MAP',
        'snapshots_created': 4, 'snapshots_completed': 4,
        'archive': str(archive), 'archive_sha256': hashlib.sha256(encrypted).hexdigest(),
        'volume_data_backed_up': True, 'application_consistency': 'NOT_QUIESCED',
        'snapshot_cutoff': 'PER_VOLUME_START_TIME_IN_ENCRYPTED_MAP',
        'restoration_runtime': 'NOT_TESTED', 'independent_map_copy': 'NOT_RUN',
        'retained_snapshot_storage_cost': 'CONTINUES_UNTIL_SEPARATE_APPROVED_CLEANUP'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=('capture', 'status', 'restore'))
    for name in ('gap-archive', 'gap-sha256', 'key-file', 'contract', 'delete-manifest', 'output-parent'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--resume-journal')
    p.add_argument('--wait-seconds', type=int, default=600)
    p.add_argument('--sops')
    p.add_argument('--age-keygen')
    p.add_argument('--kubeconfig', help='Existing scoped context, only if captured PV omits filesystem metadata')
    p.add_argument('--snapshot-map')
    p.add_argument('--snapshot-map-sha256')
    p.add_argument('--new-handoff')
    a = p.parse_args()
    os.umask(0o077)
    out = Path(tempfile.mkdtemp(prefix='rosa-monitoring-snapshots-', dir=a.output_parent))
    r = {'status': 'STOP_MONITORING_SNAPSHOT_CAPTURE', 'deletion_ready': False,
         'cluster_mutations': 0, 'SQL': 0, 'plan_repeated': False, 'automatic_retry': False}
    try:
        need(0 <= a.wait_seconds <= 1800, 'BOUNDED_SNAPSHOT_WAIT_REQUIRED')
        manifest = json.loads(B.private_file(a.delete_manifest, 1048576))
        need(manifest.get('action') == 'delete' and re.fullmatch(r'[0-9]{12}', manifest.get('account_id', '')),
             'ACCEPTED_ROSA_MANIFEST_REQUIRED')
        binary = B.sops_binary(a, out)
        if a.mode == 'capture':
            archive = B.private_file(a.gap_archive, B.MAX_BYTES*3)
            need(hashlib.sha256(archive).hexdigest() == a.gap_sha256, 'ACCEPTED_GAP_ARCHIVE_HASH_REQUIRED')
            gap = json.loads(B.crypt(binary, archive, decrypt=True, key_file=a.key_file))
            need(gap.get('purpose') == 'RESTORATION_MISSING_INPUTS_NOT_OLD_BACKUP_REPEAT', 'ACCEPTED_GAP_SOURCE_REQUIRED')
            r['restoration_review'] = restoration_review(gap)
        caller = aws(['sts', 'get-caller-identity'], out)
        need(caller.get('Account') == manifest['account_id']
             and ':assumed-role/seokpan-tf-rosa/' in caller.get('Arn', ''), 'EXISTING_ROSA_PURPOSE_ROLE_REQUIRED')
        if a.mode == 'restore':
            r.update(restore_volumes(a, manifest, out, binary, r))
        elif a.mode == 'status':
            need(a.resume_journal, 'EXISTING_SNAPSHOT_JOURNAL_REQUIRED')
            journal = json.loads(B.private_file(a.resume_journal, 1048576))
            need(journal['account_id'] == manifest['account_id'] and journal['cluster_id'] == manifest['cluster_id']
                 and journal['gap_sha256'] == a.gap_sha256, 'JOURNAL_SCOPE_REQUIRED')
        else:
            state = json.loads(B.private_file(Path(a.delete_manifest).parent/'before.tfstate'))
            handoff = state.get('outputs', {}).get('platform_handoff', {}).get('value', {})
            need(handoff.get('cluster_id') == manifest['cluster_id'] and handoff.get('infra_id'), 'SAVED_INFRA_OWNERSHIP_REQUIRED')
            rows = gap['volume_preservation_plan']
            pvs = {o['metadata']['uid']: o for o in gap['snapshots'] if o.get('kind') == 'PersistentVolume'}
            filesystem_cache = {}
            for row in rows:
                original = pvs[row['pv_uid']]['spec']
                row.update(filesystem=original['csi'].get('fsType'),
                    access_modes=original['accessModes'], volume_mode=original.get('volumeMode', 'Filesystem'),
                    storage_class=original['storageClassName'])
                if not row['filesystem']:
                    need(a.kubeconfig, 'EXISTING_CONTEXT_FOR_MISSING_STORAGECLASS_METADATA_REQUIRED')
                    if row['storage_class'] not in filesystem_cache:
                        B.private_file(a.kubeconfig, 262144)
                        sc = subprocess.run(['oc', '--kubeconfig', a.kubeconfig, '--request-timeout=20s',
                            'get', 'storageclass', row['storage_class'], '-o', 'json'], capture_output=True, timeout=30)
                        need(sc.returncode == 0, 'STORAGECLASS_FILESYSTEM_READ_FAILED_NO_GUESS')
                        cls = json.loads(sc.stdout)
                        need(cls.get('provisioner') == 'ebs.csi.aws.com', 'EXPECTED_EBS_STORAGECLASS_REQUIRED')
                        filesystem_cache[row['storage_class']] = cls.get('parameters', {}).get('csi.storage.k8s.io/fstype')
                    row['filesystem'] = filesystem_cache[row['storage_class']]
                # Validate restoration parameters before preserving paid data.
                restore_declarations(dict(row, size_gib=1), 'vol-00000000000000000', 'preflight-only')
            observed = aws(['ec2', 'describe-volumes', '--volume-ids', *[r['volume_id'] for r in rows]], out)
            rows = validate_sources(rows, observed, handoff['infra_id'])
            # All four IAM checks finish before the first snapshot is created.
            run = 'rosa-'+hashlib.sha256(a.gap_sha256.encode()).hexdigest()[:16]
            aws(['ec2', 'describe-snapshots', '--dry-run'], out, allow_dry_run=True)
            preflight_snapshot_writes(rows, run, out)
            attempt = Path(a.gap_archive).parent/'monitoring-snapshot-attempt.NO_AUTOMATIC_RETRY.json'
            exclusive(attempt, {'gap_sha256': a.gap_sha256, 'run': run, 'result_directory': str(out)})
            journal = {'schema': 1, 'run': run, 'account_id': manifest['account_id'],
                'cluster_id': manifest['cluster_id'], 'infra_id': handoff['infra_id'],
                'gap_sha256': a.gap_sha256, 'volumes': rows}
            save(out/'snapshot-journal.json', journal)
            for number, row in enumerate(rows):
                exclusive(out/('snapshot-'+str(number)+'-attempt.json'), {'volume_id': row['volume_id']})
                created = aws(snapshot_args(row, run), out)
                need(re.fullmatch(r'snap-[0-9a-f]+', created.get('SnapshotId', '')), 'SNAPSHOT_CREATE_OUTCOME_UNKNOWN')
                row['snapshot_id'] = created['SnapshotId']
                save(out/'snapshot-journal.json', journal)
                r['snapshots_created'] = number+1
        if a.mode != 'restore':
            r.update(finish(a, journal, out, binary))
    except Exception as error:
        r['reason'] = str(error) if type(error) is RuntimeError else type(error).__name__
    # A narrow handoff always exposes accepted ciphertext only, never a key or plaintext.
    try:
        need(a.mode != 'restore', 'RESTORATION_IS_NOT_A_NEW_PRESERVATION_HANDOFF')
        gap_raw = B.private_file(a.gap_archive, B.MAX_BYTES*3)
        need(hashlib.sha256(gap_raw).hexdigest() == a.gap_sha256, 'HANDOFF_GAP_HASH_MISMATCH')
        archives = [{'path': str(Path(a.gap_archive).resolve()), 'sha256': a.gap_sha256, 'kind': 'restoration-gaps'}]
        if r.get('status') == 'PASS_FOUR_ENCRYPTED_EBS_SNAPSHOTS_AND_RESTORE_MAP':
            archives.append({'path': r['archive'], 'sha256': r['archive_sha256'], 'kind': 'monitoring-snapshots'})
        handoff = Path(a.output_parent)/'rosa-preservation-handoff-latest.json'
        need(not handoff.is_symlink(), 'HANDOFF_SYMLINK_REJECTED')
        temporary = out/'handoff.json'
        save(temporary, {'schema': 2, 'archives': archives, 'snapshots_status': r['status'], 'deletion_ready': False})
        os.replace(temporary, handoff)
        r['independent_handoff_file'] = str(handoff)
    except Exception:
        r['independent_handoff_file'] = 'NOT_WRITTEN'
    r['private_result_directory'] = str(out)
    save(out/'receipt.json', r)
    print(json.dumps(r, indent=2))
    return 0 if r['status'].startswith(('PASS_', 'PENDING_')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
