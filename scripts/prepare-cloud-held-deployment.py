#!/usr/bin/env python3
"""Prepare a private Cloud render; never apply objects or start applications.

Use the accepted connection receipt. Do not decrypt secrets, retry SQL, refresh
ECR credentials, run Terraform, or modify cluster objects. Kubernetes discovery
reads may occur during client dry-run; this is not server admission validation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

SOURCE = '5294e77af873ec5318bbb7f1a1097d68208d03fa'
APP = 'c2dad254cc407431092691f9ea5892f8324f1520'
RELEASE = 'rel-cloud-20261014T034654Z-c2dad254cc40-55ad5f3b'
REGISTRY = '847835841591.dkr.ecr.ap-northeast-2.amazonaws.com/'
DIGESTS = {'backend': 'sha256:733f52a6f5a53c31e792cbe1d254221ff8ff86828e8c3afdc65d207e2e5fb18d',
           'frontend': 'sha256:7068a36f55427639b7b7bf8a9c5795689b891e7ec334bfd306d8cd03c5f049ff'}
FILES = {
    'apps/base/kustomization.yaml': 'bd9b8f9d48ffeb0e0c371cbfec912a1305a92116',
    'apps/base/backend.yaml': 'a643d9a58ada595a94fc138946b35bda284d218b',
    'apps/base/frontend.yaml': '8df77bc2bc6296778e8b45f31bc9fdc83d254a8b',
    'apps/base/services.yaml': '1700e00269cb337f37ff03ce4525cc45424507d1',
    'apps/base/runtime.env': 'cc0ad0a14b01c1ca0333a51c08efd7c7f17908df',
    'apps/overlays/cloud/runtime/kustomization.yaml': 'e4af16ad38259b6b8e8c740e37f1086b1866e401',
    'apps/overlays/cloud/runtime/routes.yaml': '2b38d35bf77bbc056be81c2dd0da6d56537c9276',
    'apps/overlays/cloud/runtime/release-source.yaml': '51a5371fd9640c29880dcd01a56e712675c22b1e',
    'apps/overlays/cloud/runtime/runtime.env': '681d72859faab3aee061c5ea369f623a7e439c12',
    'releases/' + RELEASE + '.json': '363787a7d40f9c8b83bb01719361a0a8523548a0',
}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def read_json(path):
    require(path.is_file() and not path.is_symlink(), 'REGULAR_INPUT_REQUIRED')
    return json.loads(path.read_text())


def blob_sha(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def runtime_values(text, contract):
    values = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        require(sep and key not in values, 'DUPLICATE_OR_INVALID_RUNTIME_KEY')
        require(not any(x in key for x in ('PASSWORD', 'TOKEN', 'DATABASE_URL')), 'SECRET_IN_NON_SECRET_RUNTIME')
        values[key] = value
    require(values.get('SEOKPAN_CONNECTION_PROFILE') == 'cloud' and
            values.get('SEOKPAN_GAME_LIFECYCLE_MODE') == 'captured', 'CLOUD_RUNTIME_PROFILE')
    origin = contract.get('route_origin_candidate')
    require(isinstance(origin, str) and re.fullmatch(r'https://seokpan-cloud\.[a-z0-9.-]+\.openshiftapps\.com', origin),
            'EXPECTED_ROSA_ROUTE_CANDIDATE')
    require(json.loads(values.get('SEOKPAN_ALLOWED_ORIGINS', 'null')) == [origin], 'ORIGIN_CONTRACT_MISMATCH')
    require('INPUT_REQUIRED' not in text and 'input-required.invalid' not in text, 'UNRESOLVED_RUNTIME_INPUT')
    return origin, values


def hold_patch(name):
    return {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name},
            'spec': {'replicas': 0, 'template': {'spec': {
                'serviceAccountName': 'seokpan-cloud-runtime',
                'imagePullSecrets': [{'name': 'seokpan-cloud-ecr-pull'}],
                'nodeSelector': {'node-role.kubernetes.io/worker': ''},
                'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {
                    'nodeSelectorTerms': [{'matchExpressions': [
                        {'key': 'node-role.kubernetes.io/' + role, 'operator': 'DoesNotExist'}
                        for role in ('infra', 'master', 'control-plane')]}]}}}}}}}


def prepare_source(source, runtime, origin):
    overlay = source / 'apps/overlays/cloud/runtime'
    kustomization = (overlay / 'kustomization.yaml').read_text()
    require(kustomization.count('namespace: cloud-input-required') == 1 and
            kustomization.count('  - path: release-source.yaml') == 1, 'SOURCE_PATCH_LOCATION_CHANGED')
    kustomization = kustomization.replace('namespace: cloud-input-required', 'namespace: seokpan-cloud')
    kustomization = kustomization.replace('  - path: release-source.yaml',
                                        '  - path: release-source.yaml\n  - path: platform-hold.yaml')
    (overlay / 'kustomization.yaml').write_text(kustomization)
    (overlay / 'platform-hold.yaml').write_text('\n---\n'.join(json.dumps(hold_patch(n)) for n in ('backend', 'frontend')) + '\n')
    (overlay / 'runtime.env').write_text(runtime)
    routes = (overlay / 'routes.yaml').read_text()
    require(routes.count('rosa-route-host-input-required.invalid') == 3, 'ROUTE_SOURCE_CHANGED')
    (overlay / 'routes.yaml').write_text(routes.replace('rosa-route-host-input-required.invalid', origin[8:]))
    return overlay


def validate(items, origin):
    expected = {('Deployment', 'backend'), ('Deployment', 'frontend'), ('Service', 'backend'),
                ('Service', 'frontend'), ('Route', 'frontend'), ('Route', 'backend-api'), ('Route', 'backend-ws')}
    actual = set()
    cms = []
    for obj in items:
        kind, meta = obj.get('kind'), obj.get('metadata', {})
        require(meta.get('namespace') == 'seokpan-cloud', 'NAMESPACE_CHANGED')
        key = (kind, meta.get('name'))
        require(key not in actual, 'DUPLICATE_OBJECT')
        actual.add(key)
        require(kind in ('Deployment', 'Service', 'Route', 'ConfigMap'), 'UNEXPECTED_RESOURCE_KIND')
        if kind == 'Deployment':
            name = meta['name']
            require(name in DIGESTS and obj['spec'].get('replicas') == 0, 'APPLICATION_MUST_STAY_ZERO')
            ann = meta.get('annotations', {})
            require(ann.get('seokpan.io/app-source-sha') == APP and ann.get('seokpan.io/release-id') == RELEASE,
                    'CANDIDATE_PROVENANCE_MISMATCH')
            spec = obj['spec']['template']['spec']
            require(spec.get('serviceAccountName') == 'seokpan-cloud-runtime' and
                    spec.get('imagePullSecrets') == [{'name': 'seokpan-cloud-ecr-pull'}] and
                    spec.get('automountServiceAccountToken') is False, 'RUNTIME_SERVICE_ACCOUNT_CONTRACT')
            require(spec.get('nodeSelector', {}).get('node-role.kubernetes.io/worker') == '', 'COMPUTE_ONLY_SELECTOR')
            affinity = spec.get('affinity', {}).get('nodeAffinity', {}).get('requiredDuringSchedulingIgnoredDuringExecution', {})
            terms = affinity.get('nodeSelectorTerms', [])
            require(terms == hold_patch(name)['spec']['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'],
                    'COMPUTE_ONLY_AFFINITY')
            containers = spec.get('containers', [])
            require(len(containers) == 1 and containers[0].get('image') == REGISTRY + 'seokpan-fnd-' + name + '@' + DIGESTS[name],
                    'APPROVED_IMAGE_PAIR_REQUIRED')
            if name == 'backend':
                refs = {e['valueFrom']['secretKeyRef']['name'] for e in containers[0].get('env', [])
                        if 'secretKeyRef' in e.get('valueFrom', {})}
                require(refs == {'backend-db-runtime', 'backend-redis-runtime'}, 'EXISTING_SECRET_NAMES_REQUIRED')
        elif kind == 'Route':
            require(obj['spec'].get('host') == origin.removeprefix('https://') and
                    obj['spec'].get('tls', {}).get('termination') == 'edge' and
                    obj['spec']['tls'].get('insecureEdgeTerminationPolicy') == 'Redirect', 'ROUTE_TLS_CONTRACT')
        elif kind == 'ConfigMap':
            cms.append(obj)
    require(len(items) == 8 and len(cms) == 1 and cms[0]['metadata']['name'].startswith('backend-config-'), 'EXACT_HELD_OBJECT_SET')
    require(actual - {('ConfigMap', cms[0]['metadata']['name'])} == expected, 'EXACT_HELD_OBJECT_NAMES')
    return {'deployments': 2, 'replicas': 0, 'services': 2, 'routes': 3, 'configmaps': 1}


def run(command, folder, name):
    result = subprocess.run(command, capture_output=True, timeout=90, stdin=subprocess.DEVNULL)
    (folder / (name + '.stderr')).write_bytes(result.stderr)
    require(result.returncode == 0, name.upper() + '_FAILED_PROTECTED_LOG')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_root', type=Path)
    parser.add_argument('completed_connection', type=Path)
    args = parser.parse_args()
    folder = None
    try:
        os.umask(0o077)
        require(os.name == 'posix' and __import__('pwd').getpwuid(os.getuid()).pw_name == 'jth', 'USE_JTH_CONTROLLER')
        root = args.input_root.resolve()
        connection = args.completed_connection.resolve()
        require(connection.parent == root and connection.name.startswith('cloud-connection-'), 'CONNECTION_PATH_SCOPE')
        receipt = read_json(connection / 'receipt.json')
        contract = read_json(connection / 'cloud-platform-inputs.json')
        cluster = contract.get('cluster_id', '')
        require(re.fullmatch(r'[a-z0-9]{10,64}', cluster) and contract.get('namespace') == 'seokpan-cloud', 'RECEIPT_IDENTITY')
        handoff = read_json(root / 'management' / cluster / 'data-platform-handoff.json')
        require(handoff.get('cluster_id') == cluster and handoff.get('binding_enabled') is True, 'COMPLETED_DATA_HANDOFF_REQUIRED')
        require(receipt.get('status') == 'CLOUD_CONNECTION_PREFLIGHT_COMPLETED' and receipt.get('app_started') is False,
                'COMPLETED_CONNECTION_RECEIPT_REQUIRED')
        require(receipt.get('runtime_inputs_supply', {}).get('status') == 'PASS_CLOUD_RUNTIME_SECRETS_AND_CA_SUPPLIED',
                'COMPLETED_PROTECTED_INPUT_SUPPLY_REQUIRED')
        runtime = (connection / 'runtime.env.INCOMPLETE').read_text()
        origin, values = runtime_values(runtime, contract)
        folder = Path(tempfile.mkdtemp(prefix='cloud-held-render-', dir=root))
        source = folder / 'source'
        for name, expected in FILES.items():
            url = 'https://raw.githubusercontent.com/seokpan/seokpan-hybrid-gitops/' + SOURCE + '/' + name
            with urllib.request.urlopen(url, timeout=25) as response:
                raw = response.read(262145)
            require(len(raw) <= 262144 and blob_sha(raw) == expected, 'PINNED_SOURCE_CONTENT_CHANGED')
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        candidate = read_json(source / ('releases/' + RELEASE + '.json'))
        require(candidate.get('source', {}).get('app_sha') == APP and candidate.get('release_id') == RELEASE,
                'RELEASE_SOURCE_MISMATCH')
        require(all(candidate['images'][k]['ecr_digest'] == v for k, v in DIGESTS.items()), 'RELEASE_ECR_PAIR_MISMATCH')
        overlay = prepare_source(source, runtime, origin)
        oc = Path.home() / '.local/share/seokpan/oc-cli-4.20/oc'
        require(oc.is_file(), 'EXISTING_OC_REQUIRED')
        kube = root / 'management' / cluster / 'kubeconfig'
        context = json.loads(run([str(oc), '--kubeconfig', str(kube), 'config', 'view', '--minify', '-o', 'json'], folder, 'context'))
        require(context['clusters'][0]['cluster']['server'].rstrip('/') == handoff['api_url'].rstrip('/'),
                'WRONG_MANAGEMENT_CONTEXT')
        rendered = run([str(oc), 'kustomize', str(overlay)], folder, 'kustomize')
        rendered_path = folder / 'held-render.yaml'
        rendered_path.write_bytes(rendered)
        converted = run([str(oc), '--kubeconfig', str(kube), '--request-timeout=20s', 'create',
                         '--dry-run=client', '--validate=false', '-f', str(rendered_path), '-o', 'json'], folder, 'client-dry-run')
        objects = json.loads(converted)
        items = objects.get('items') if objects.get('kind') == 'List' else [objects]
        counts = validate(items, origin)
        (folder / 'held-render.json').write_bytes(converted)
        pending = ['C_DB_ADMIN_AUTH_AND_ACTUAL_LIMIT', 'B_POOL_ACCEPTANCE', 'C_FINAL_IMPORT_AND_RUNTIME_ACCOUNTS',
                   'B_ORIGINAL_WRITER_STOP_AND_CUTOVER', 'B_RESOURCE_AND_ACTIVATION_REVIEW']
        summary = {'status': 'PASS_PRIVATE_HELD_RENDER_CLIENT_VALIDATION', 'source_sha': SOURCE,
                   'source_blob_checks': len(FILES), 'object_counts': counts,
                   'sql_auth_retry': False, 'secret_decryption': False, 'terraform': 'NOT_RUN',
                   'cluster_mutations': 0, 'application_started': False, 'ready_for_apply': False,
                   'server_admission': 'NOT_TESTED', 'discovery_reads': 'POSSIBLE_CLIENT_DRY_RUN_ONLY',
                   'pool_adopted': contract.get('database_pool_adopted') is True,
                   'pending': pending, 'render_sha256': hashlib.sha256(rendered).hexdigest(),
                   'connection_receipt_sha256': hashlib.sha256((connection / 'receipt.json').read_bytes()).hexdigest()}
        (folder / 'receipt.json').write_text(json.dumps(summary, sort_keys=True))
        print(json.dumps(summary, sort_keys=True))
        print('Private deployment preparation directory: ' + str(folder))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r'[A-Z0-9_]+', str(exc)) else type(exc).__name__
        print(json.dumps({'status': 'STOP_PRIVATE_HELD_RENDER', 'reason': reason, 'cluster_mutations': 0,
                          'sql_auth_retry': False, 'application_started': False}, sort_keys=True))
        if folder is not None:
            print('Private deployment preparation directory: ' + str(folder))
        return 1


if __name__ == '__main__':
    sys.exit(main())
