"""Restore only the accepted GitHub provider through the managed ROSA API.

No direct OAuth patch, credential rotation, role grant, database action or app Sync.
An uncertain write is never retried. Reads can reconcile an accepted provider.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

NAME = 'seokpan-github'
CLIENT = 'Ov23liEzwxdkzauh2rkX'


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def safe(error):
    text = str(error)
    return text if re.fullmatch(r'[A-Z][A-Z0-9_:.-]{1,180}', text) else type(error).__name__


def public_provider(provider):
    github = provider.get('github', {})
    return {'name': provider.get('name'), 'type': provider.get('type'),
            'mapping_method': provider.get('mapping_method', provider.get('mappingMethod')),
            'client_id': github.get('client_id', github.get('clientID')),
            'organizations': sorted(github.get('organizations') or []),
            'teams': sorted(github.get('teams') or []), 'hostname': github.get('hostname') or ''}


def desired_provider(provider, secret):
    need(provider.get('name') == NAME and provider.get('type') == 'GitHub', 'EXACT_ARCHIVED_GITHUB_PROVIDER_REQUIRED')
    github = provider['github']
    need(github.get('clientID') == CLIENT and not github.get('hostname') and not github.get('ca'),
         'EXISTING_PUBLIC_GITHUB_CLIENT_REQUIRED')
    organizations, teams = github.get('organizations') or [], github.get('teams') or []
    need(bool(organizations) != bool(teams), 'UNCHANGED_RESTRICTED_ORGANIZATION_OR_TEAM_REQUIRED')
    need(provider.get('mappingMethod') in ('claim', 'lookup', 'add'), 'EXISTING_IDENTITY_MAPPING_REQUIRED')
    need(isinstance(secret, str) and 10 <= len(secret) <= 1024 and not re.search(r'\s', secret),
         'EXISTING_PROTECTED_CLIENT_SECRET_REQUIRED')
    details = {'client_id': CLIENT, 'client_secret': secret}
    details['organizations' if organizations else 'teams'] = organizations or teams
    return {'kind': 'IdentityProvider', 'name': NAME, 'type': 'GithubIdentityProvider',
            'mapping_method': provider['mappingMethod'], 'login': True, 'github': details}


def decision(items, desired):
    matches = [p for p in items if p.get('name') == NAME]
    need(len(matches) <= 1, 'DUPLICATE_MANAGED_PROVIDER_NO_WRITE')
    if not matches:
        return 'create', None
    current = matches[0]
    need(public_provider(current) == public_provider(desired), 'EXISTING_MANAGED_PROVIDER_DIFFERS_NO_OVERWRITE')
    need(isinstance(current.get('id'), str) and re.fullmatch(r'[A-Za-z0-9_-]+', current['id']), 'MANAGED_PROVIDER_ID_REQUIRED')
    return ('enable_login' if current.get('login') is False else 'reuse'), current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatch', required=True, type=Path)
    parser.add_argument('--expected-cluster-id', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    out = None
    report = {'status': 'STOP_MANAGED_GITHUB_AUTH', 'sql_calls': 0, 'app_sync_started': False,
              'direct_oauth_patches': 0, 'role_grants': 0, 'secret_values_printed': False,
              'automatic_write_retry': False, 'managed_provider_writes': 0}
    phase = 'EXISTING_CURRENT_CLUSTER_BINDING'
    try:
        import pwd
        import fcntl
        need(args.execute and pwd.getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH_AND_EXECUTE')
        dispatch = json.loads(args.dispatch.read_text())
        config = json.loads(Path(dispatch['config']).read_text())
        root = Path(config['evidence_root'])
        cid = config['current_cluster_id']
        need(cid == args.expected_cluster_id and re.fullmatch(r'[a-z0-9]{10,64}', cid)
             and cid != config['source_cluster_id'], 'EXACT_CURRENT_NEW_CLUSTER_REQUIRED')
        folder = root/'held-restoration'/cid
        scripts = Path(config['create']['restore_driver']).parent
        source = scripts/'prepare-rosa-checkpoint.py'
        need(hashlib.sha256(source.read_bytes()).hexdigest() == config['restoration']['dependency_sha256'][source.name],
             'ACCEPTED_PRIVATE_ARCHIVE_READER_REQUIRED')
        spec = importlib.util.spec_from_file_location('accepted_private_archive', source)
        B = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(B)
        read = lambda p: json.loads(B.private_file(p, 16*1024*1024))
        global_lock = (args.dispatch.parent/'managed-github-auth.lock').open('a')
        fcntl.flock(global_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pointer = args.dispatch.parent/'current-managed-github-auth.json'
        if pointer.exists():
            prior = read(pointer)
            need(prior.get('cluster_id') == cid, 'AUTH_REPAIR_CLUSTER_BINDING_CHANGED')
            out = Path(prior['directory'])
        else:
            out = Path(tempfile.mkdtemp(prefix='managed-github-auth-', dir=root))
            (out/'binding.json').write_text(json.dumps({'cluster_id': cid, 'provider': NAME}))
            pointer.write_text(json.dumps({'cluster_id': cid, 'directory': str(out)}))
        lock = (out/'repair.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        management = read(folder/'management.complete.json')
        need(management.get('cluster_id') == cid, 'CURRENT_MANAGEMENT_BINDING_REQUIRED')
        kube = management['result']['kubeconfig']
        oc = config['auth']['oc_binary']
        def oc_read(*argv):
            proc = subprocess.run([oc, '--kubeconfig', kube, '--request-timeout=15s', *argv, '-o', 'json'],
                                  capture_output=True, timeout=25)
            if proc.returncode:
                text = proc.stderr.decode(errors='replace').lower()
                raise RuntimeError('CURRENT_AUTH_READ_FORBIDDEN' if 'forbidden' in text else
                                   'CURRENT_AUTH_CONTEXT_LOGIN_REQUIRED' if 'unauthorized' in text else 'CURRENT_AUTH_READ_FAILED')
            return json.loads(proc.stdout)
        platform = read(config['create']['last_platform_handoff'])
        local = subprocess.run([oc, '--kubeconfig', kube, 'config', 'view', '--minify', '-o', 'json'],
                               capture_output=True, timeout=10)
        need(local.returncode == 0, 'CURRENT_MANAGEMENT_CONTEXT_REQUIRED')
        cluster = json.loads(local.stdout)['clusters'][0]['cluster']
        need(cluster['server'].rstrip('/') == platform['api_url'].rstrip('/') and not cluster.get('insecure-skip-tls-verify'),
             'EXACT_TRUSTED_CURRENT_API_REQUIRED')
        live = oc_read('get', 'oauth', 'cluster')
        report['live_provider_names_before'] = [x['name'] for x in live['spec'].get('identityProviders', [])]
        # Read the accepted encrypted definition for this repair; do not rerun backup acceptance.
        phase = 'LOAD_ACCEPTED_PROVIDER_AND_EXISTING_SECRET'
        restoration = config['restoration']
        cipher = B.private_file(restoration['recipe_archive'], B.MAX_BYTES*3)
        need(hashlib.sha256(cipher).hexdigest() == restoration['recipe_sha256'], 'PINNED_PROVIDER_DEFINITION_REQUIRED')
        recipe = json.loads(B.crypt(restoration['sops_binary'], cipher, decrypt=True, key_file=restoration['key_file']))
        oauth, = [o for o in recipe['objects'] if o['kind'] == 'OAuth' and o['metadata']['name'] == 'cluster']
        provider, = [p for p in oauth['spec']['identityProviders'] if p['name'] == NAME]
        name = provider['github']['clientSecret']['name']
        archived, = [o for o in recipe['objects'] if o['kind'] == 'Secret'
                     and o['metadata'].get('namespace') == 'openshift-config' and o['metadata']['name'] == name]
        secret = oc_read('get', 'secret', name, '-n', 'openshift-config')
        value = secret.get('data', {}).get('clientSecret')
        need(value and value == archived.get('data', {}).get('clientSecret'), 'SAME_EXISTING_GITHUB_SECRET_REQUIRED_NO_ROTATION')
        wanted = desired_provider(provider, base64.b64decode(value, validate=True).decode())
        phase = 'CURRENT_ROSA_MANAGED_PROVIDER'
        rosa = config['auth']['rosa_cli']
        token_process = subprocess.run([rosa, 'token', '--generate'], capture_output=True, timeout=35)
        if token_process.returncode:
            print(json.dumps({'phase': 'REDHAT_DEVICE_LOGIN_REQUIRED', 'AWS_MFA': False}), flush=True)
            with open('/dev/tty', 'r+') as tty:
                login = subprocess.run([rosa, 'login', '--use-device-code'], stdin=tty, stdout=tty, stderr=tty, timeout=300)
            need(login.returncode == 0, 'PERSONAL_REDHAT_LOGIN_NOT_COMPLETED')
            token_process = subprocess.run([rosa, 'token', '--generate'], capture_output=True, timeout=35)
        need(token_process.returncode == 0, 'CURRENT_REDHAT_ACCESS_REQUIRED')
        token = token_process.stdout.decode().strip()
        need(re.fullmatch(r'[A-Za-z0-9_.-]+', token) and len(token) > 100, 'ACCESS_TOKEN_FORMAT_REQUIRED')
        def api(path, method='GET', payload=None):
            need(path.startswith('/api/clusters_mgmt/v1/clusters/'+cid), 'EXACT_MANAGED_CLUSTER_PATH_REQUIRED')
            request = urllib.request.Request('https://api.openshift.com'+path,
                data=None if payload is None else json.dumps(payload).encode(), method=method,
                headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json', 'Accept': 'application/json'})
            # Do not forward bearer credentials through an unexpected redirect.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args): return None
            try:
                with urllib.request.build_opener(NoRedirect).open(request, timeout=35) as response:
                    return json.loads(response.read(4*1024*1024))
            except urllib.error.HTTPError as error:
                raise RuntimeError('OCM_'+method+'_HTTP_'+str(error.code)) from None
            except (TimeoutError, OSError):
                raise RuntimeError('OCM_'+method+'_OUTCOME_UNKNOWN_NO_WRITE_RETRY') from None
        path = '/api/clusters_mgmt/v1/clusters/'+cid
        remote = api(path)
        need(remote.get('id') == cid and remote.get('state') == 'ready'
             and remote.get('api', {}).get('url', '').rstrip('/') == platform['api_url'].rstrip('/'),
             'MANAGED_CURRENT_CLUSTER_ID_API_READY_REQUIRED')
        collection = path+'/identity_providers'
        providers = api(collection+'?size=100')
        need(providers.get('total', len(providers.get('items', []))) <= 100, 'COMPLETE_MANAGED_PROVIDER_LIST_REQUIRED')
        action, selected = decision(providers.get('items', []), wanted)
        report.update(managed_provider_names_before=[p['name'] for p in providers.get('items', [])],
                      selected_action=action, provider_scope=public_provider(wanted), cluster_id=cid)
        print(json.dumps({'phase': 'MANAGED_PROVIDER_DECISION', **report}), flush=True)
        marker = out/'managed-write.attempted.json'
        if action != 'reuse':
            need(not marker.exists(), 'PRIOR_MANAGED_WRITE_NOT_CONFIRMED_NO_RETRY')
            marker.write_text(json.dumps({'cluster_id': cid, 'name': NAME, 'action': action}))
            report['managed_provider_write_attempted'] = True
            if action == 'create':
                api(collection, 'POST', wanted)
            else:
                api(collection+'/'+selected['id'], 'PATCH', {'login': True})
            report['managed_provider_writes'] = 1
        after = api(collection+'?size=100')
        accepted_action, accepted = decision(after.get('items', []), wanted)
        need(accepted_action == 'reuse', 'MANAGED_PROVIDER_WRITE_REQUIRES_RECONCILIATION_NO_RETRY')
        report['managed_provider_id'] = accepted['id']
        phase = 'WAIT_FOR_MANAGED_AUTH_CONFIGURATION'
        end = time.monotonic()+360
        while time.monotonic() < end:
            current = oc_read('get', 'oauth', 'cluster')
            rows = [p for p in current['spec'].get('identityProviders', []) if p.get('name') == NAME]
            co = oc_read('get', 'clusteroperator', 'authentication')
            conditions = {x['type']: x['status'] for x in co.get('status', {}).get('conditions', [])}
            rendered = len(rows) == 1 and public_provider(rows[0]) == {
                **public_provider(wanted), 'type': 'GitHub'}
            print(json.dumps({'phase': phase, 'provider_rendered': rendered, 'conditions': conditions}), flush=True)
            if rendered and conditions.get('Available') == 'True' and conditions.get('Progressing') == 'False' and conditions.get('Degraded') == 'False':
                break
            time.sleep(15)
        else:
            raise RuntimeError('MANAGED_PROVIDER_ACCEPTED_RENDER_PENDING_NO_RECREATE')
        report.update(status='PASS_MANAGED_GITHUB_PROVIDER_RENDERED_NEW_BROWSER_LOGIN_REQUIRED',
                      github_login_runtime='PENDING_BROWSER', app_sync_started=False,
                      app_source_preparation_repeated=False,
                      next='EXISTING_ARGO_OPENSHIFT_LOGIN_THEN_PREPARED_SINGLE_SYNC', bounded_wait_seconds=360)
        (out/'accepted.json').write_text(json.dumps(report, indent=2))
    except Exception as error:
        report.update(status='STOP_MANAGED_GITHUB_AUTH', phase=phase, reason=safe(error))
    print(json.dumps(report, indent=2), flush=True)
    if out is not None:
        (out/'result.json').write_text(json.dumps(report, indent=2))
        print('Private managed GitHub auth directory: '+str(out), flush=True)
    return 0 if report['status'].startswith('PASS_') else 2


if __name__ == '__main__':
    raise SystemExit(main())
