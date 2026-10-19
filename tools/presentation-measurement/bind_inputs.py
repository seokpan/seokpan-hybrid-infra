"""Bind existing accepted launch outputs to measurement files. Local files only.

This does not create/requery a cluster, accept business results, sample SQL,
start a browser or trigger a rollout. Paths on a different host are not probed.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage_clock import module, read, save
from timeline import need, sha

MERGED_BASE = 'df71963f975cae2925f8783472a8c143b5f264af'
LAUNCH_SHA256 = '4564cb9ce5bb0892024dd7f9fb4de5e2d9d8cb851f651bb3baa21813218f3f82'
UUID = r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}'
PATH_FIELDS = {'controller_python', 'controller_measurement_dir', 'controller_work',
               'controller_trigger_prefix', 'observer_node', 'observer_playwright_module',
               'db_mariadb', 'db_credential_file', 'db_ca_file', 'db_target_reference',
               'db_interval_seconds', 'db_duration_seconds', 'extra_recovery_seconds'}


def load_launch(infra):
    path = Path(infra)/'tools/cloud-launch/cloud_launch.py'
    # Git's CRLF checkout conversion is not a source change.
    normalized = path.read_bytes().replace(b'\r\n', b'\n')
    need(hashlib.sha256(normalized).hexdigest() == LAUNCH_SHA256, 'REVIEWED_MERGED_LAUNCH_SOURCE_REQUIRED')
    return module('measurement_bound_launch', path)


def bind(launch, package, stage, ready, business_ref, paths, controller):
    need(set(paths) == PATH_FIELDS, 'EXACT_PATH_INPUT_FIELDS_REQUIRED')
    p, c, source, desired, release = launch.package(package, stage)
    need(c['cluster_id'] != launch.OLD_CLUSTER, 'DELETED_CLUSTER_REJECTED')
    need(re.fullmatch(UUID, ready.get('application_uid') or '') is not None, 'ACTUAL_APPLICATION_UID_REQUIRED')
    need(re.fullmatch(UUID, c['namespace_uid']) is not None, 'ACTUAL_NAMESPACE_UID_REQUIRED')
    need(isinstance(business_ref, str) and business_ref.strip(), 'EXISTING_NORMAL_BUSINESS_EVIDENCE_REQUIRED')
    # Reuse the approved package/Ready/business-reference contract; no live query.
    plan = launch.measurement(package, stage, ready, business_ref)
    need(ready['source_sha256'] == launch.digest(source), 'SAME_SOURCE_READY_REQUIRED')
    binding = {'cluster_id': c['cluster_id'], 'namespace_uid': c['namespace_uid'],
               'application_uid': ready['application_uid'], 'source_sha256': launch.digest(source),
               'normalized_source_sha256': launch.digest(launch.normalize_source(source))}
    profiles = {}
    for scenario in ('baseline', 'rolling'):
        value = copy.deepcopy(plan)
        value.update(run_id=c['run_id']+'-'+stage+'-'+scenario, scenario=scenario,
                     extra_recovery_seconds=paths['extra_recovery_seconds'], binding=binding)
        need(len(value['run_id']) <= 80, 'RUN_ID_WITH_SCENARIO_TOO_LONG')
        profiles[scenario+'.json'] = value
    extra = paths['extra_recovery_seconds']
    need(type(extra) is int and 0 <= extra <= 120, 'BOUNDED_EXTRA_RECOVERY_REQUIRED')
    images, replicas = {}, {}
    for name in ('backend', 'frontend'):
        dep, = [o for o in desired['items'] if o['kind'] == 'Deployment' and o['metadata']['name'] == name]
        images[name] = dep['spec']['template']['spec']['containers'][0]['image']
        replicas[name] = dep['spec']['replicas']
        need(replicas[name] == plan['runtime']['replicas'][name]
             and images[name].endswith('@'+plan['release'][name+'_digest']), 'PACKAGE_PLAN_IMAGE_REPLICA_MISMATCH')
    need(type(paths['db_interval_seconds']) is int and 2 <= paths['db_interval_seconds'] <= 60
         and type(paths['db_duration_seconds']) is int and 1 <= paths['db_duration_seconds'] <= 900,
         'BOUNDED_DB_OBSERVATION_REQUIRED')
    required_controller = ('oc', 'personal_kubeconfig')
    need(all(isinstance(controller.get(k), str) and controller[k].strip() for k in required_controller),
         'EXISTING_PERSONAL_CONTEXT_PATHS_REQUIRED')
    rolling = {'actor': 'tjung03', 'namespace': launch.NS, 'application': launch.APP,
               'oc': controller['oc'], 'personal_kubeconfig': controller['personal_kubeconfig'],
               'api': c['api_url'], 'namespace_uid': c['namespace_uid'], 'application_uid': ready['application_uid'],
               'run_id': c['run_id']+'-'+stage+'-rolling', 'source_sha256': binding['source_sha256'],
               'normalized_source_sha256': binding['normalized_source_sha256'],
               'expected_images': images, 'expected_replicas': replicas, **c['window']}
    missing = [k for k in sorted(PATH_FIELDS-{'controller_trigger_prefix', 'db_interval_seconds',
               'db_duration_seconds', 'extra_recovery_seconds'}) if not isinstance(paths[k], str) or not paths[k].strip()]
    prefix = paths['controller_trigger_prefix']
    need(prefix is None or (isinstance(prefix, list) and all(isinstance(x, str) and x for x in prefix)), 'ARGV_PREFIX_ARRAY_REQUIRED')
    for key in ('controller_python', 'controller_measurement_dir', 'controller_work'):
        need(paths[key] is None or (isinstance(paths[key], str) and re.fullmatch(r'/[A-Za-z0-9_./-]+', paths[key])),
             'ABSOLUTE_CONTROLLER_PATH_WITHOUT_SHELL_METACHARACTERS_REQUIRED')
    # [] explicitly selects execution on the Controller itself; null is undecided.
    if prefix is None: missing.append('controller_trigger_prefix')
    argv = []
    if prefix is not None and all(paths[k] for k in ('controller_python', 'controller_measurement_dir', 'controller_work')):
        # POSIX Controller paths are inputs, even when this binder runs on Windows.
        m = paths['controller_measurement_dir'].rstrip('/')
        w = paths['controller_work'].rstrip('/')
        argv = prefix+[paths['controller_python'], '-I', m+'/rolling_control.py', 'trigger',
                       '--config', w+'/rolling-config.json', '--work', w+'/rolling-work', '--execute']
    trigger = {'scope': 'ONE_BACKEND_ROLL', 'binding': {k: binding[k] for k in ('namespace_uid', 'application_uid')}, 'argv': argv}
    result = {**profiles, 'rolling-config.json': rolling, 'trigger.json': trigger}
    for scenario in ('baseline', 'rolling'):
        result['db-'+scenario+'.json'] = {
            'binding': {**binding, 'scenario': scenario, 'run_id': profiles[scenario+'.json']['run_id']},
            'target_reference': paths['db_target_reference'], 'mariadb': paths['db_mariadb'],
            'credential_file': paths['db_credential_file'], 'ca_file': paths['db_ca_file'],
            'database_host': c['database_host'], 'accounts': ['identity_svc', 'game_svc'],
            'interval_seconds': paths['db_interval_seconds'], 'duration_seconds': paths['db_duration_seconds'],
            'not_after': c['window']['not_after']}
    result['binding-receipt.json'] = {
        'status': 'PREPARED_LOCAL_MEASUREMENT_BINDINGS_NOT_RUNTIME' if not missing else 'PENDING_LOCAL_EXECUTION_PATH_INPUTS',
        'binding': binding, 'stage': stage, 'input_fingerprint': sha({'paths': paths, 'ready': ready, 'business_ref': business_ref}),
        'missing_paths': missing, 'observer_paths': {k: paths[k] for k in ('observer_node', 'observer_playwright_module')},
        'remaining_current_db_limits_and_reservation': True,
        'no_live_state_queried': True, 'business_reference_independently_verified': False,
        'db_credentials_read': False, 'api_calls': 0, 'sql_calls': 0, 'recreate_invoked': False}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--infra', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True, help='Existing prestart work with package and stage-ready receipt')
    parser.add_argument('--stage', choices=('single', 'shared', 'target'), required=True)
    parser.add_argument('--business-ref', required=True, help='Existing business acceptance evidence; not an approval switch')
    parser.add_argument('--prestart-config', type=Path, required=True)
    parser.add_argument('--paths', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='New private output directory; transfer to configured Controller path')
    args = parser.parse_args()
    try:
        # Validate everything before writing; no uncertain partial output is reused.
        result = bind(load_launch(args.infra), args.work/'package', args.stage,
                      read(args.work/(args.stage+'-ready.json')), args.business_ref,
                      read(args.paths), read(args.prestart_config))
        args.out.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, value in result.items(): save(args.out/name, value)
        save(args.out/'SHA256SUMS.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.out.iterdir()})
        receipt = result['binding-receipt.json']
        print(json.dumps({'status': receipt['status'], 'missing_paths': receipt['missing_paths'],
                          'files': len(result), 'api_calls': 0, 'sql_calls': 0, 'recreate_invoked': False}))
        return 2 if receipt['missing_paths'] else 0
    except Exception as error:
        print(json.dumps({'status': 'STOP_MEASUREMENT_BIND', 'reason': str(error) if type(error) is RuntimeError else type(error).__name__,
                          'api_calls': 0, 'sql_calls': 0, 'recreate_invoked': False}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
