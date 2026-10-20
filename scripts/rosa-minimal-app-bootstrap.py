"""Reuse completed management/data stages; prepare held apps without history restore."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')
    path.chmod(0o600)


def workload_config(text):
    if not text.strip():
        return json.dumps({'enableUserWorkload': True})
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    else:
        need(isinstance(value, dict), 'MONITORING_CONFIG_MAPPING_REQUIRED')
    if isinstance(value, dict):
        value['enableUserWorkload'] = True
        return json.dumps(value)
    lines = text.splitlines()
    matches = [(i, re.fullmatch(r'enableUserWorkload\s*:\s*(true|false)(\s*(?:#.*)?)', line))
               for i, line in enumerate(lines)]
    matches = [(i, m) for i, m in matches if m]
    need(len(matches) <= 1, 'DUPLICATE_USER_WORKLOAD_SETTING_STOP')
    if matches:
        i, m = matches[0]
        lines[i] = 'enableUserWorkload: true' + m.group(2)
    else:
        need(not re.search(r'enableUserWorkload', text), 'UNRECOGNIZED_USER_WORKLOAD_SETTING_STOP')
        need(sum(line.strip() == '---' for line in lines) <= 1, 'MULTI_DOCUMENT_MONITORING_CONFIG_STOP')
        if '---' in lines:
            lines.insert(lines.index('---') + 1, 'enableUserWorkload: true')
        else:
            lines.insert(0, 'enableUserWorkload: true')
    return '\n'.join(lines) + '\n'


def completed(value, cluster, recipe_hash):
    need(value.get('cluster_id') == cluster and value.get('recipe_sha256') == recipe_hash,
         'COMPLETED_STAGE_BINDING_CHANGED')
    return value['result']


def run_core(r, step):
    step('gitops-operator', r.operator)
    step('namespaces', lambda: r.apply_actions({'FRESH_NAMESPACE'}))
    step('human-configuration', r.humans)
    step('argocd-spec', lambda: r.apply_actions({'ARGO_CR_SPEC_RESTORE'}))


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--source-scripts', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    a = parser.parse_args()
    os.umask(0o077)
    a.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    phase = 'LOAD_COMPLETED_BINDINGS'
    cid = None
    r = None
    result = {}
    try:
        spec = importlib.util.spec_from_file_location('held', a.source_scripts/'rosa-held-restoration.py')
        H = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(H)
        c = H.L.read_json(a.config)
        cid = c['current_cluster_id']
        need(c['scope'] == 'ROSA_ONLY' and cid != c['source_cluster_id'], 'EXISTING_NEW_CLUSTER_REQUIRED')
        d = Path(c['evidence_root'])/'held-restoration'/cid
        need(d.is_dir() and not d.is_symlink(), 'EXISTING_RESTORATION_DIRECTORY_REQUIRED')
        lock = (d/'restore.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recipe_hash = c['restoration']['recipe_sha256']
        management = completed(H.L.read_json(d/'management.complete.json'), cid, recipe_hash)
        worker = completed(H.L.read_json(d/'fresh-worker-binding.complete.json'), cid, recipe_hash)
        need(worker.get('verified') is True, 'ACCEPTED_WORKER_BINDING_REQUIRED')
        h = H.L.read_json(worker['handoff'])
        need(h['cluster_id'] == cid and h['state'] == 'ready' and h['binding_enabled'] is True,
             'BOUND_READY_PLATFORM_HANDOFF_REQUIRED')
        need(Path(management['kubeconfig']).is_file(), 'EXISTING_MANAGEMENT_CONTEXT_REQUIRED')
        for name, expected in c['restoration']['dependency_sha256'].items():
            need(H.L.sha(a.source_scripts/name) == expected, 'RESTORATION_DEPENDENCY_CHANGED')
        cipher = H.B.private_file(c['restoration']['recipe_archive'], H.B.MAX_BYTES*3)
        need(hashlib.sha256(cipher).hexdigest() == recipe_hash, 'PINNED_RECIPE_REQUIRED')
        recipe = json.loads(H.B.crypt(c['restoration']['sops_binary'], cipher, decrypt=True,
                                     key_file=c['restoration']['key_file']))
        need(recipe.get('purpose') == 'ROSA_HELD_RESTORATION_RECIPE', 'HELD_RECIPE_REQUIRED')
        r = H.Restore(c, h, recipe, d)
        r.kube = Path(management['kubeconfig'])
        c['create']['last_platform_handoff'] = worker['handoff']
        c['restoration']['bound_inputs'] = worker['bound_inputs']
        c['restoration']['minimum_app_bootstrap_receipt'] = str(a.out/'receipt.json')

        phase = 'CURRENT_MANAGEMENT_CONTEXT'
        who = subprocess.run([r.oc, '--kubeconfig', str(r.kube), '--request-timeout=15s', 'whoami'],
                             capture_output=True, timeout=25)
        if who.returncode:
            error = who.stderr.decode('utf-8', 'replace').lower()
            need(any(x in error for x in ('unauthorized', 'must be logged in', 'provide credentials')),
                 'MANAGEMENT_CONTEXT_ERROR_PRIVATE_REVIEW_REQUIRED')
            admin = H.L.read_json(management['initial_admin_credentials'])
            need(H.W.Runner(d).login(r.oc, h['api_url'], admin, r.kube), 'CURRENT_MANAGEMENT_LOGIN_REQUIRED')
        ctx = r.oc_call(['config', 'view', '--minify', '-o', 'json'])
        cluster = ctx['clusters'][0]['cluster']
        need(cluster['server'].rstrip('/') == h['api_url'].rstrip('/')
             and not cluster.get('insecure-skip-tls-verify'), 'SAME_TRUSTED_API_REQUIRED')

        def step(name, fn):
            nonlocal phase
            phase = name
            print(json.dumps({'phase': name, 'cluster_id': cid}), flush=True)
            return r.stage(name, fn)

        run_core(r, step)
        phase = 'WAIT_FOR_CURRENT_ARGO'
        H.G.wait_for(lambda: (v if v and v.get('status', {}).get('phase') == 'Available' else None)
                     if (v := r.get({'kind':'ArgoCD','metadata':{'name':recipe['contract']['argo_name'],
                         'namespace':recipe['contract']['argo_namespace']}})) else None, 600, 'MINIMAL_ARGO')
        step('platform-inputs', lambda: r.apply_actions({
            'PLATFORM_AND_OBSERVABILITY_DECLARATION', 'ZERO_REPLICA_DECLARATION', 'PROJECT_DECLARATION'}))

        def enable_monitoring():
            obj = {'apiVersion':'v1','kind':'ConfigMap',
                   'metadata':{'name':'cluster-monitoring-config','namespace':'openshift-monitoring'}}
            live = r.get(obj)
            data = dict((live or {}).get('data') or {})
            data['config.yaml'] = workload_config(data.get('config.yaml', ''))
            obj['data'] = data
            return r.put(obj)

        step('minimal-current-monitoring-config', enable_monitoring)
        step('held-applications', lambda: r.apply_actions({'HELD_APPLICATION_NO_SYNC'}))
        held = step('held-acceptance', r.held_acceptance)
        phase = 'CURRENT_VOLUME_BINDING_OBSERVATION'
        pvcs = r.oc_call(['get','pvc','-n','openshift-monitoring','-o','json'])
        volume_rows = []
        for pvc in pvcs['items']:
            state = pvc.get('status', {}).get('phase')
            name = pvc['metadata']['name']
            if state == 'Bound':
                pv = r.oc_call(['get','pv',pvc['spec']['volumeName'],'-o','json'])
                claim = pv.get('spec', {}).get('claimRef', {})
                need(claim.get('uid') == pvc['metadata']['uid']
                     and claim.get('namespace') == 'openshift-monitoring', 'CURRENT_PVC_BINDING_MISMATCH')
            volume_rows.append({'name':name,'phase':state,'uid':pvc['metadata']['uid']})
        oauth = r.oc_call(['get','route','oauth-openshift','-n','openshift-authentication','-o','json'])
        callbacks = {name:'https://'+oauth['spec']['host']+'/oauth2callback/'+name
                     for name in recipe['contract']['identity_providers']}
        save(a.out/'new-oauth-callbacks.json', callbacks)
        pending = c['restoration'].get('accepted_new_oauth_callbacks') != callbacks
        temp = a.config.with_suffix('.minimal-new.json')
        save(temp, c)
        os.replace(temp, a.config)
        result = {'status':'PENDING_NEW_PERSONAL_LOGIN_CALLBACK' if pending else 'PASS_MINIMAL_HELD_APP_PLATFORM',
            'cluster_id':cid,'management_and_worker_binding_reused':True,
            'historical_monitoring_restore':'DEFERRED_NOT_RETRIED',
            'full_old_object_comparison':'DEFERRED_NOT_CLAIMED',
            'current_pvcs':volume_rows,'monitoring_config_enabled':True,
            'monitoring_scrape_runtime':'PENDING_APP_AND_EXPLICIT_MONITORING_SYNC',
            'callbacks_to_register':callbacks if pending else {},
            'personal_login_required':True,'protected_data_checks':'PENDING_PURPOSE_TLS_SCHEMA',
            'application_started':False,'SQL':0,'automatic_sync':False,
            'management_kubeconfig':str(r.kube),'platform_handoff':worker['handoff'],
            'restoration_directory':str(d),'held':held}
    except Exception as error:
        reason = str(error)
        result = {'status':'STOP_MINIMAL_APP_BOOTSTRAP','phase':phase,'cluster_id':cid,
            'reason':reason if re.fullmatch(r'[A-Za-z0-9_.:-]{1,220}',reason) else type(error).__name__,
            'automatic_retry':False,'application_started':False,'SQL':0,
            'private_logs':str(r.out) if r else str(a.out)}
    save(a.out/'receipt.json', result)
    print(json.dumps(result, indent=2), flush=True)
    print('Private bootstrap directory: '+str(a.out), flush=True)
    return 1 if result['status'].startswith('STOP_') else 0


if __name__ == '__main__':
    raise SystemExit(main())
