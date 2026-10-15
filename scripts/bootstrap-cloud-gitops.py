#!/usr/bin/env python3
"""Controller jth: install only the selected Cloud GitOps Operator via Ansible.

No Root/Application, Sync, human OAuth/RBAC change, SQL, app activation or Terraform.
OLM creates the Operator's own installation resources, including its RBAC.
OLM uses Manual approval; only the exact 1.21.4 InstallPlan is approved.
Existing managed objects must match; no force apply, deletion or version fallback.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

VERSION = '1.21.4'
CSV = 'openshift-gitops-operator.v' + VERSION
PACKAGE = 'openshift-gitops-operator'
NS = PACKAGE
CHANNEL = 'gitops-1.21'
INPUT_SHA = '66e433911fb676fc811a67cdbbb446ab9ccfe5a7da9307d47d9e09abf57f5f3b'
LABEL = {'seokpan.io/bootstrap': 'cloud-gitops'}


class Stop(Exception):
    pass


def need(ok, reason):
    if not ok:
        raise Stop(reason)


def load(p):
    need(p.is_file() and not p.is_symlink(), 'EXISTING_REGULAR_FILE_REQUIRED')
    return json.loads(p.read_text())


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def catalog_candidate(packages):
    rows = [p for p in packages.get('items', [])
            if p.get('metadata', {}).get('name') == PACKAGE
            and p.get('status', {}).get('catalogSource') == 'redhat-operators'
            and p['status'].get('catalogSourceNamespace') == 'openshift-marketplace']
    need(len(rows) == 1, 'ONE_OFFICIAL_GITOPS_CATALOG_REQUIRED')
    channels = [c for c in rows[0]['status'].get('channels', []) if c.get('name') == CHANNEL]
    need(len(channels) == 1, 'SELECTED_CHANNEL_NOT_AVAILABLE')
    c = channels[0]
    available = c.get('currentCSV') == CSV or any(
        e.get('name') == CSV and e.get('version') == VERSION for e in c.get('entries', []))
    need(available, 'SELECTED_1_21_4_NOT_ADVERTISED_NO_VERSION_FALLBACK')
    return {'channel': CHANNEL, 'starting_csv': CSV, 'catalog': 'redhat-operators'}


def manifests():
    return [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NS,
         'labels': {**LABEL, 'openshift.io/cluster-monitoring': 'true'}}},
        {'apiVersion': 'operators.coreos.com/v1', 'kind': 'OperatorGroup',
         'metadata': {'name': PACKAGE, 'namespace': NS, 'labels': LABEL},
         'spec': {'upgradeStrategy': 'Default'}},
        {'apiVersion': 'operators.coreos.com/v1alpha1', 'kind': 'Subscription',
         'metadata': {'name': PACKAGE, 'namespace': NS, 'labels': LABEL},
         'spec': {'channel': CHANNEL, 'installPlanApproval': 'Manual',
                  'name': PACKAGE, 'source': 'redhat-operators',
                  'sourceNamespace': 'openshift-marketplace', 'startingCSV': CSV}}]


def same_owned(live, expected):
    need(live.get('metadata', {}).get('labels', {}).get('seokpan.io/bootstrap') ==
         'cloud-gitops', 'EXISTING_INSTALL_OBJECT_NOT_OWNED_NO_TAKEOVER')
    if expected['kind'] != 'Namespace':
        need(live.get('spec') == expected['spec'], 'EXISTING_INSTALL_SPEC_CHANGED')
    else:
        need(live['metadata'].get('labels', {}).get('openshift.io/cluster-monitoring') ==
             'true', 'EXISTING_NAMESPACE_LABEL_CHANGED')


def exact_plan(plan, ref):
    need(plan.get('metadata', {}).get('name') == ref.get('name') and
         plan['metadata'].get('uid') == ref.get('uid'), 'INSTALLPLAN_REFERENCE_CHANGED')
    need(plan.get('spec', {}).get('approval') == 'Manual' and
         plan['spec'].get('clusterServiceVersionNames') == [CSV],
         'UNEXPECTED_INSTALLPLAN_VERSION_OR_DEPENDENCIES')
    need(plan.get('status', {}).get('phase') in ('RequiresApproval', 'Installing', 'Complete'),
         'INSTALLPLAN_UNRESOLVED_OR_FAILED')
    lookups = plan.get('status', {}).get('bundleLookups', [])
    need(bool(lookups) and all(
        x.get('identifier') == CSV and
        x.get('catalogSourceRef', {}).get('name') == 'redhat-operators' and
        x['catalogSourceRef'].get('namespace') == 'openshift-marketplace' for x in lookups),
        'INSTALLPLAN_OFFICIAL_BUNDLE_NOT_PROVEN')


def approval_patch(plan, ref):
    exact_plan(plan, ref)
    version = plan.get('metadata', {}).get('resourceVersion')
    need(isinstance(version, str) and bool(version), 'INSTALLPLAN_RESOURCE_VERSION_REQUIRED')
    need(plan['spec'].get('approved') is False, 'INSTALLPLAN_APPROVAL_STATE_CHANGED')
    return [{'op': 'test', 'path': '/metadata/uid', 'value': ref['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': version},
            {'op': 'test', 'path': '/spec/approval', 'value': 'Manual'},
            {'op': 'test', 'path': '/spec/clusterServiceVersionNames', 'value': [CSV]},
            {'op': 'test', 'path': '/spec/approved', 'value': False},
            {'op': 'replace', 'path': '/spec/approved', 'value': True}]


def approve_exact_installplan(runner, ref):
    # Re-read the selected object, then bind the atomic patch to all reviewed fields.
    # resourceVersion also guards bundleLookups/status changes between GET and PATCH.
    current = runner.get('installplans.operators.coreos.com', ref['name'], NS)
    exact_plan(current, ref)
    if current['spec'].get('approved') is True:
        return
    patch = approval_patch(current, ref)
    runner.call(['patch', 'installplans.operators.coreos.com', ref['name'], '-n', NS,
                 '--type=json', '-p', json.dumps(patch)], 'approve-exact-installplan')


PLAYBOOK = json.dumps([{'name': 'Install the selected Cloud GitOps bootstrap declarations',
    'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
    'tasks': [{'name': 'Create the missing exact declaration without changing existing objects',
        'ansible.builtin.command': {'argv': ['{{ oc_binary }}', '--request-timeout=45s',
            'create', '-f', '{{ item }}', '-o', 'name']},
        'environment': {'KUBECONFIG': '{{ kubeconfig }}'},
        'loop': '{{ manifest_paths }}', 'changed_when': True}]}], indent=2)


class Runner:
    def __init__(self, oc, kube, folder):
        self.oc, self.folder = str(oc), folder
        self.env = dict(os.environ, KUBECONFIG=str(kube))
        self.counter = 0

    def call(self, args, label, payload=None):
        self.counter += 1
        r = subprocess.run([self.oc, '--request-timeout=30s', *args], input=payload,
                           capture_output=True, env=self.env, timeout=50)
        (self.folder / (label + '.stderr.log')).write_bytes(r.stderr)
        need(r.returncode == 0, label.upper() + '_FAILED_READ_PROTECTED_LOG')
        return r.stdout

    def get(self, resource, name=None, namespace=None, optional=False):
        args = ['get', resource] + ([name] if name else [])
        args += ['-n', namespace] if namespace else []
        args += ['--ignore-not-found'] if optional else []
        args += ['-o', 'json']
        raw = self.call(args, 'get-' + str(self.counter))
        return json.loads(raw) if raw.strip() else None


def wait_for(action, seconds, label):
    deadline = time.monotonic() + seconds
    last_print = 0
    while True:
        value = action()
        if value:
            return value
        need(time.monotonic() < deadline, label + '_TIMEOUT_PRESERVE_PARTIAL_STATE')
        if time.monotonic() - last_print > 45:
            print(json.dumps({'status': 'WAITING', 'phase': label}), flush=True)
            last_print = time.monotonic()
        time.sleep(10)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input_root', type=Path)
    args = p.parse_args()
    os.umask(0o077)
    need(os.name == 'posix' and __import__('pwd').getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH')
    root = args.input_root.resolve()
    need(root.parent == Path.home() / '.local/share' and root.name.startswith('seokpan-rosa-plan-inputs-'), 'PROTECTED_INPUT_ROOT_REQUIRED')
    need(digest(root / 'protected/inputs.tfvars.json') == INPUT_SHA, 'INPUTS_CHANGED')
    prior = load(root / 'b01-b05-b08-independent-attempt.json')
    prior_dir = Path(prior['result_directory']).resolve()
    need(prior_dir.parent == root and prior_dir.name.startswith('cloud-independent-'), 'PREVIOUS_RESULT_PATH_SCOPE')
    receipt = load(prior_dir / 'summary.json')
    need(receipt.get('status') == 'INDEPENDENT_BATCH_COMPLETED' and
         receipt['steps']['B08_management_inventory']['argoproj_api_resources'] == 0,
         'ACCEPTED_INDEPENDENT_RECEIPT_REQUIRED')
    mgmt = root / 'management/2tejeb2farjaaoef1e74asdf6amrtpu3'
    handoff = load(mgmt / 'data-platform-handoff.json')
    oc_dir = Path.home() / '.local/share/seokpan/oc-cli-4.20'
    oc = oc_dir / 'oc'
    need(oc.is_file() and not oc.is_symlink() and digest(oc) == load(oc_dir / 'receipt.json')['oc_sha256'], 'VERIFIED_EXISTING_OC_REQUIRED')
    need(shutil.which('ansible-playbook'), 'EXISTING_ANSIBLE_PLAYBOOK_REQUIRED_NO_INSTALL_ATTEMPTED')
    import fcntl
    lock = os.open(root / 'cloud-continuation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    folder = Path(tempfile.mkdtemp(prefix='cloud-gitops-bootstrap-', dir=root))
    r = Runner(oc, mgmt / 'kubeconfig', folder)
    result = {'status': 'RUNNING', 'application_created': False, 'app_started': False,
              'human_idp_or_human_rolebinding_changes': False,
              'terraform_or_sql_changes': False, 'completed_tests_repeated': False,
              'version': VERSION}
    def save():
        (folder / 'receipt.json').write_text(json.dumps(result, sort_keys=True) + '\n')
    try:
        need(r.call(['whoami', '--show-server'], 'target').decode().strip().rstrip('/') == handoff['api_url'].rstrip('/'), 'WRONG_CLUSTER')
        result['catalog'] = catalog_candidate(r.get('packagemanifests', namespace='openshift-marketplace'))
        # Discover conflicts before the first write; no community operator takeover.
        subs = r.call(['get', 'subscriptions.operators.coreos.com', '-A', '-o', 'json'], 'all-subscriptions')
        owned_subscription = False
        for s in json.loads(subs).get('items', []):
            if 'gitops' in s.get('spec', {}).get('name', '') or 'argocd' in s.get('spec', {}).get('name', ''):
                need(s.get('metadata', {}).get('namespace') == NS and s['metadata'].get('name') == PACKAGE,
                     'OTHER_ARGO_OR_GITOPS_SUBSCRIPTION_EXISTS')
                same_owned(s, manifests()[2])
                owned_subscription = True
        apis = r.call(['api-resources', '--api-group=argoproj.io', '-o', 'name'], 'preinstall-argo-conflict')
        need(not apis.strip() or owned_subscription, 'UNOWNED_ARGO_API_EXISTS_NO_TAKEOVER')
        desired = manifests()
        resource = {'Namespace': 'namespaces', 'OperatorGroup': 'operatorgroups.operators.coreos.com',
                    'Subscription': 'subscriptions.operators.coreos.com'}
        existing_ns = r.get('namespaces', NS, optional=True)
        missing = []
        for i, obj in enumerate(desired):
            live = (existing_ns if i == 0 else r.get(resource[obj['kind']], obj['metadata']['name'], NS, optional=True)) if existing_ns else None
            if live:
                same_owned(live, obj)
            else:
                path = folder / ('install-' + str(i) + '.json')
                path.write_text(json.dumps(obj))
                missing.append(str(path))
        # Register the precise callback without creating or changing any IdP.
        route = r.get('routes.route.openshift.io', 'oauth-openshift', 'openshift-authentication')
        host = route['spec']['host']
        need(isinstance(host, str) and host.endswith('.openshiftapps.com') and '/' not in host, 'OAUTH_ROUTE_HOST_FORMAT')
        (folder / 'github-oauth-registration.json').write_text(json.dumps({
            'provider_name': 'seokpan-github', 'callback_url': 'https://' + host + '/oauth2callback/seokpan-github',
            'team': 'NOT_VERIFIED', 'oauth_app': 'NOT_VERIFIED', 'client_secret': 'DO_NOT_SEND_TO_CHAT'}) + '\n')
        if missing:
            playbook = folder / 'bootstrap.yml'
            playbook.write_text(PLAYBOOK)
            variables = folder / 'ansible-vars.json'
            variables.write_text(json.dumps({'oc_binary': str(oc), 'kubeconfig': str(mgmt / 'kubeconfig'), 'manifest_paths': missing}))
            cmd = ['ansible-playbook', '-i', 'localhost,', str(playbook), '--extra-vars', '@' + str(variables)]
            print(json.dumps({'status': 'STARTING_MINIMAL_ANSIBLE_BOOTSTRAP', 'objects': len(missing),
                              'approval': 'Manual', 'version': VERSION}), flush=True)
            with (folder / 'ansible.log').open('wb') as out:
                done = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, env=r.env, timeout=180)
            need(done.returncode == 0, 'ANSIBLE_FAILED_PARTIAL_STATE_PRESERVED')
        sub = wait_for(lambda: (s if s.get('status', {}).get('installPlanRef') else None)
                       if (s := r.get('subscriptions.operators.coreos.com', PACKAGE, NS)) else None,
                       420, 'OLM_RESOLUTION')
        same_owned(sub, desired[2])
        ref = sub['status']['installPlanRef']
        def resolved_plan():
            value = r.get('installplans.operators.coreos.com', ref['name'], NS)
            need(value.get('status', {}).get('phase') != 'Failed', 'INSTALLPLAN_FAILED_PRESERVE_INSTALLATION')
            return value if value.get('status', {}).get('phase') in ('RequiresApproval', 'Installing', 'Complete') else None
        plan = wait_for(resolved_plan, 420, 'EXACT_INSTALLPLAN_RESOLUTION')
        exact_plan(plan, ref)
        (folder / 'installplan-reviewed.json').write_text(json.dumps(plan))
        if not plan['spec'].get('approved'):
            approve_exact_installplan(r, ref)
        def csv_ready():
            c = r.get('clusterserviceversions.operators.coreos.com', CSV, NS, optional=True)
            if c:
                need(c.get('status', {}).get('phase') != 'Failed', 'CSV_FAILED_PRESERVE_INSTALLATION')
                if c.get('status', {}).get('phase') == 'Succeeded':
                    need(c.get('spec', {}).get('version') == VERSION, 'INSTALLED_VERSION_CHANGED')
                    return c
        wait_for(csv_ready, 900, 'EXACT_CSV_SUCCEEDED')
        def argo_ready():
            a = r.get('argocds.argoproj.io', 'openshift-gitops', 'openshift-gitops', optional=True)
            return a if a and a.get('status', {}).get('phase') == 'Available' else None
        wait_for(argo_ready, 600, 'DEFAULT_ARGO_AVAILABLE')
        # No Root or Application is submitted: app sync remains unrequested.
        result.update(status='PASS_CLOUD_GITOPS_OPERATOR_AND_DEFAULT_ARGO',
                      operator_csv=CSV, argo_available=True, install_approval='Manual',
                      normal_github_login='NOT_CONFIGURED', initial_admin_retired=False)
    except Exception as exc:
        result.update(status='STOP_CLOUD_GITOPS_BOOTSTRAP', reason=str(exc) if isinstance(exc, Stop) else type(exc).__name__,
                      automatic_retry=False, partial_resources='PRESERVED_DO_NOT_DELETE_OR_REINSTALL')
    finally:
        result['oc_commands'] = r.counter
        result['finished_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        save()
        print(json.dumps(result, sort_keys=True), flush=True)
        print('Private GitOps result directory: ' + str(folder), flush=True)
        os.close(lock)
    return 0 if result['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status': 'STOP_BEFORE_GITOPS_INSTALL',
                          'reason': str(exc) if isinstance(exc, Stop) else type(exc).__name__}))
        raise SystemExit(1)

