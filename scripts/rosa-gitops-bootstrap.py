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


