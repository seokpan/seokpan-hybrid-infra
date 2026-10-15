#!/usr/bin/env python3
"""Connect the observed GitHub B account to ROSA and scoped Cloud Argo access.

Run manually as Controller jth with the existing management activation sourced.
No app creation/sync, IdP change, Secret access, initial-admin retirement or AWS IAM.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

USER = 'tjung03'
NS = 'openshift-gitops'
GROUP = 'seokpan-cloud-gitops-operators'
ROLE = 'role:seokpan-cloud-operator'
PROJECT = 'seokpan-cloud-app'
LABEL = 'seokpan.io/management-purpose'
LINES = [
    f'p, {ROLE}, applications, get, {PROJECT}/*, allow',
    f'p, {ROLE}, applications, sync, {PROJECT}/*, allow',
    f'p, {ROLE}, projects, get, {PROJECT}, allow',
    f'g, {GROUP}, {ROLE}',
]


class Stop(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Stop(reason)


def accepted_target(root):
    matches = []
    for management in (root / 'management').iterdir():
        receipt = management / 'data-binding/receipt.json'
        if receipt.is_file() and json.loads(receipt.read_text()).get('status') in (
                'PASS_DATA_CONNECTION_RULES_APPLIED', 'PASS_DATA_CONNECTION_RULES_ALREADY_PRESENT'):
            matches.append(management)
    require(len(matches) == 1, 'ONE_PREVIOUSLY_ACCEPTED_MANAGEMENT_TARGET_REQUIRED')
    management = matches[0]
    handoff = json.loads((management / 'data-platform-handoff.json').read_text())
    require(handoff.get('cluster_id') == management.name and handoff.get('binding_enabled') is True,
            'ACCEPTED_HANDOFF_BINDING_CHANGED')
    api = handoff.get('api_url')
    require(isinstance(api, str) and api.startswith('https://api.') and api.endswith('.openshiftapps.com:6443'),
            'ACCEPTED_HANDOFF_API_FORMAT')
    return handoff['cluster_id'], api


def require_identity(user, identity):
    meta = user.get('metadata', {})
    require(meta.get('name') == USER, 'EXPECTED_PERSONAL_USER_REQUIRED')
    refs = [s for s in user.get('identities', []) if s.startswith('seokpan-github:')]
    require(len(refs) == 1, 'ONE_OBSERVED_GITHUB_IDENTITY_REQUIRED')
    require(identity.get('metadata', {}).get('name') == refs[0], 'IDENTITY_REFERENCE_CHANGED')
    require(identity.get('providerName') == 'seokpan-github', 'GITHUB_PROVIDER_CHANGED')
    require(identity.get('user', {}).get('name') == USER and
            identity.get('user', {}).get('uid') == meta.get('uid') and meta.get('uid'),
            'USER_IDENTITY_UID_MISMATCH')


def require_group(group):
    if group is None:
        return
    require(group.get('metadata', {}).get('labels', {}).get(LABEL) == 'cloud-gitops',
            'EXISTING_GROUP_OWNERSHIP_REVIEW_REQUIRED')
    require(group.get('users', []) == [USER], 'GROUP_HAS_OTHER_MEMBERS_OR_CHANGED_SCOPE')


def candidate_rbac(argo):
    spec = argo.get('spec', {})
    require(spec.get('sso', {}).get('provider') == 'dex' and
            spec.get('sso', {}).get('dex', {}).get('openShiftOAuth') is True,
            'EXISTING_OPENSHIFT_DEX_REQUIRED')
    require(not spec.get('oidcConfig'), 'OTHER_OIDC_CONFIGURATION_REVIEW_REQUIRED')
    meta = argo.get('metadata', {})
    require(not meta.get('annotations', {}).get('argocd.argoproj.io/tracking-id') and
            not meta.get('labels', {}).get('app.kubernetes.io/instance'),
            'ARGO_CONFIGURATION_ALREADY_GITOPS_MANAGED')
    old = spec.get('rbac')
    require(isinstance(old, dict), 'EXISTING_RBAC_OBJECT_REQUIRED')
    require(old.get('defaultPolicy', '') == '', 'BROAD_DEFAULT_POLICY_REVIEW_REQUIRED')
    require(old.get('scopes', '').replace(' ', '') in ('[groups]', '["groups"]', "['groups']"),
            'EXISTING_GROUP_SCOPES_REQUIRED')
    policy = old.get('policy', '')
    require(isinstance(policy, str), 'POLICY_FORMAT')
    current = [s.strip() for s in policy.splitlines() if s.strip()]
    owned = [s for s in current if GROUP in s or ROLE in s]
    require(not owned or (set(owned) == set(LINES) and len(owned) == len(LINES)),
            'OWNED_ARGO_POLICY_CONFLICT')
    new = dict(old)
    if not owned:
        new['policy'] = policy.rstrip() + '\n' + '\n'.join(LINES) + '\n'
    return old, new


def patch_for(argo, old, new):
    return [
        {'op': 'test', 'path': '/metadata/resourceVersion',
         'value': argo['metadata']['resourceVersion']},
        {'op': 'test', 'path': '/spec/rbac', 'value': old},
        {'op': 'replace', 'path': '/spec/rbac', 'value': new},
    ]


class Runner:
    def __init__(self, folder, oc):
        self.folder, self.oc, self.count = folder, oc, 0
        self.mutations = []

    def save(self, name, obj):
        (self.folder / name).write_text(json.dumps(obj, indent=2) + '\n', encoding='utf-8')

    def run(self, argv, stage, mutation=False):
        self.count += 1
        if mutation:
            self.mutations.append({'stage': stage, 'outcome': 'ATTEMPTED'})
            self.save('mutation-attempts.json', self.mutations)
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            raise Stop(stage + '_COMMAND_UNCERTAIN') from None
        stem = f'{self.count:03d}-{stage}'
        (self.folder / (stem + '.stdout')).write_text(result.stdout, encoding='utf-8')
        (self.folder / (stem + '.stderr')).write_text(result.stderr, encoding='utf-8')
        require(result.returncode == 0, stage + '_FAILED_SEE_PRIVATE_LOG')
        if mutation:
            self.mutations[-1]['outcome'] = 'EXIT_0'
            self.save('mutation-attempts.json', self.mutations)
        return result.stdout

    def oc_run(self, args, stage, mutation=False):
        return self.run([self.oc, '--request-timeout=30s', *args], stage, mutation)

    def get(self, resource, name, stage, namespace=None, optional=False):
        args = ['get', resource, name, '-o', 'json']
        if namespace:
            args += ['-n', namespace]
        if optional:
            args += ['--ignore-not-found=true']
        raw = self.oc_run(args, stage)
        return json.loads(raw) if raw.strip() else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--rosa-cli', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    folder = None
    runner = None
    phase = 'LOCAL_CONTEXT'
    try:
        os.umask(0o077)
        require(os.name == 'posix' and os.environ.get('USER') == 'jth', 'CONTROLLER_JTH_REQUIRED')
        root = args.root.expanduser().resolve()
        require(root.is_dir() and root.stat().st_uid == os.getuid(), 'OWNED_EXISTING_ROOT_REQUIRED')
        cluster, expected_server = accepted_target(root)
        require(args.rosa_cli.is_file() and os.access(args.rosa_cli, os.X_OK), 'EXISTING_ROSA_CLI_REQUIRED')
        oc = shutil.which('oc')
        require(oc, 'SOURCE_EXISTING_MANAGEMENT_ACTIVATION_FIRST')
        folder = Path(tempfile.mkdtemp(prefix='human-access-', dir=root))
        runner = Runner(folder, oc)
        phase = 'TARGET_AND_ACTUAL_IDENTITY'
        server = runner.oc_run(['whoami', '--show-server'], 'target-server').strip()
        require(server.rstrip('/') == expected_server.rstrip('/'), 'WRONG_CLUSTER_NO_MUTATIONS')
        user = runner.get('user', USER, 'observed-github-user', optional=True)
        require(user is not None, 'GITHUB_LOGIN_REQUIRED_NO_PERMISSIONS_CHANGED')
        refs = [s for s in user.get('identities', []) if s.startswith('seokpan-github:')]
        require(len(refs) == 1, 'ONE_OBSERVED_GITHUB_IDENTITY_REQUIRED')
        identity = runner.get('identity', refs[0], 'observed-identity-link')
        require_identity(user, identity)
        argo = runner.get('argocd', 'openshift-gitops', 'argo-before', namespace=NS)
        old, new = candidate_rbac(argo)
        runner.save('argo-before.json', argo)
        custom_group = runner.get('group', GROUP, 'scoped-group-before', optional=True)
        require_group(custom_group)
        runner.save('scoped-group-before.json', custom_group)
        admins = runner.get('group', 'dedicated-admins', 'platform-group-before', optional=True)
        runner.save('platform-group-before.json', admins)
        grant_needed = USER not in (admins or {}).get('users', [])
        group_doc = {'apiVersion': 'user.openshift.io/v1', 'kind': 'Group',
                     'metadata': {'name': GROUP, 'labels': {LABEL: 'cloud-gitops'}}, 'users': [USER]}
        runner.save('scoped-group.json', group_doc)
        runner.save('argo-rbac-patch.json', patch_for(argo, old, new))
        phase = 'SERVER_DRY_RUN'
        if custom_group is None:
            runner.oc_run(['create', '--dry-run=server', '-f', str(folder / 'scoped-group.json')], 'group-dry-run')
        if old != new:
            runner.oc_run(['patch', 'argocd', 'openshift-gitops', '-n', NS, '--type=json',
                           '--patch-file', str(folder / 'argo-rbac-patch.json'), '--dry-run=server'], 'argo-dry-run')
        if grant_needed:
            runner.run([str(args.rosa_cli), 'whoami'], 'existing-rosa-session')
        print(json.dumps({'status': 'PASS_PERSONAL_IDENTITY_AND_ACCESS_SCOPE', 'user': USER,
                          'openshift_role': 'dedicated-admin', 'argo_project': PROJECT,
                          'argo_actions': ['get', 'sync'], 'other_humans_granted': 0}), flush=True)
        if not args.apply:
            print(json.dumps({'status': 'PREPARED_NOT_APPLIED', 'browser_role_acceptance': 'NOT_RUN'}))
            return 0
        phase = 'PERSONAL_PLATFORM_ROLE'
        if grant_needed:
            runner.run([str(args.rosa_cli), 'grant', 'user', 'dedicated-admin', '--user', USER,
                        '--cluster', cluster], 'grant-dedicated-admin', mutation=True)
        for attempt in range(25):
            admins = runner.get('group', 'dedicated-admins', 'platform-group-current', optional=True)
            if USER in (admins or {}).get('users', []):
                break
            if attempt % 6 == 0:
                print(json.dumps({'status': 'WAITING_PERSONAL_PLATFORM_GROUP'}), flush=True)
            time.sleep(5)
        else:
            raise Stop('PLATFORM_GROUP_NOT_RECONCILED_NO_AUTO_REGRANT')
        phase = 'SCOPED_ARGO_GROUP_AND_CR'
        if custom_group is None:
            runner.oc_run(['create', '-f', str(folder / 'scoped-group.json')], 'create-scoped-group', mutation=True)
        # Rebuild the compare-and-swap patch after OCM reconciliation, preserving current settings.
        argo = runner.get('argocd', 'openshift-gitops', 'argo-current', namespace=NS)
        old, new = candidate_rbac(argo)
        if old != new:
            runner.save('argo-rbac-patch.json', patch_for(argo, old, new))
            runner.oc_run(['patch', 'argocd', 'openshift-gitops', '-n', NS, '--type=json',
                           '--patch-file', str(folder / 'argo-rbac-patch.json')], 'patch-argo-cr', mutation=True)
        phase = 'RECONCILED_CONFIGURATION'
        require_group(runner.get('group', GROUP, 'scoped-group-after'))
        for attempt in range(25):
            cm = runner.get('configmap', 'argocd-rbac-cm', 'effective-argo-rbac', namespace=NS)
            data = cm.get('data', {})
            csv = {s.strip() for s in data.get('policy.csv', '').splitlines() if s.strip()}
            if set(LINES) <= csv and data.get('policy.default', '') == '' and 'groups' in data.get('scopes', ''):
                break
            if attempt % 6 == 0:
                print(json.dumps({'status': 'WAITING_ARGO_OPERATOR_RBAC'}), flush=True)
            time.sleep(5)
        else:
            raise Stop('ARGO_RBAC_RECONCILIATION_NOT_CONFIRMED_NO_REPATCH')
        route = runner.get('route', 'openshift-gitops-server', 'argo-route', namespace=NS)
        host = route.get('spec', {}).get('host')
        require(isinstance(host, str) and host and '/' not in host, 'ARGO_ROUTE_HOST_REQUIRED')
        receipt = {'status': 'PASS_PERSONAL_OPENSHIFT_AND_SCOPED_ARGO_CONFIGURATION', 'user': USER,
                   'openshift_role': 'dedicated-admin', 'argo_group': GROUP, 'argo_project': PROJECT,
                   'argo_actions': ['get', 'sync'], 'argo_url': 'https://' + host,
                   'normal_browser_login': 'USER_SCREENSHOT_PREVIOUS_PASS',
                   'new_role_and_argo_browser_acceptance': 'NOT_YET_VERIFIED',
                   'app_created': False, 'sync_started': False, 'initial_admin_retired': False,
                   'other_humans_granted': 0, 'completed_cluster_tests_repeated': False,
                   'finished_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()}
        runner.save('receipt.json', receipt)
        print(json.dumps(receipt), flush=True)
        return 0
    except (Stop, OSError, ValueError, TypeError, KeyError) as exc:
        reason = str(exc) if isinstance(exc, Stop) else type(exc).__name__
        receipt = {'status': 'STOP_PERSONAL_ACCESS', 'phase': phase, 'reason': reason,
                   'raw_output': 'PRIVATE_NOT_PRINTED', 'automatic_retry': False,
                   'mutations_attempted': len(runner.mutations) if runner else 0}
        if runner:
            runner.save('receipt.json', receipt)
        print(json.dumps(receipt), flush=True)
        return 2
    finally:
        if folder:
            print('Private access result directory: ' + str(folder), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
