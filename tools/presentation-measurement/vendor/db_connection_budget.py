"""Offline declared DB connection caps; never RDS observation or deployment approval."""

import argparse
import json
import sys

from offline_review import Invalid, read_json


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise Invalid('BUDGET_FIELDS_REQUIRED')


def count(value, positive=False):
    if value is None:
        return None
    if type(value) is not int or value < (1 if positive else 0):
        raise Invalid('BOUNDED_INTEGER_REQUIRED')
    return value


def review(inputs):
    exact(inputs, ('schema_version', 'input_kind', 'rds_max_connections',
                   'reserved_connections', 'pod_groups'))
    if type(inputs['schema_version']) is not int or inputs['schema_version'] != 1:
        raise Invalid('BUDGET_SCHEMA_VERSION')
    if inputs['input_kind'] not in ('candidate', 'reported-observation'):
        raise Invalid('BUDGET_INPUT_KIND')
    maximum = count(inputs['rds_max_connections'], positive=True)
    reserved = count(inputs['reserved_connections'])
    groups = inputs['pod_groups']
    exact(groups, ('steady', 'surge', 'terminating'))
    caps = {}
    pending = []
    for name, group in groups.items():
        exact(group, ('pods', 'processes_per_pod', 'engines'))
        pods = count(group['pods'])
        processes = count(group['processes_per_pod'], positive=True)
        exact(group['engines'], ('identity', 'game'))
        engine_caps = []
        for engine in group['engines'].values():
            exact(engine, ('pool_size', 'max_overflow'))
            size = count(engine['pool_size'], positive=True)
            overflow = count(engine['max_overflow'])
            engine_caps.append(None if size is None or overflow is None else size + overflow)
        if any(value is None for value in (pods, processes, *engine_caps)):
            caps[name] = None
            pending.append('POD_GROUP_INPUT_INCOMPLETE')
        else:
            caps[name] = pods * processes * sum(engine_caps)
    if maximum is None:
        pending.append('RDS_MAX_CONNECTIONS_REQUIRED')
    if reserved is None:
        pending.append('RESERVED_CONNECTIONS_REQUIRED')
    runtime = None if any(value is None for value in caps.values()) else sum(caps.values())
    required = None if runtime is None or reserved is None else runtime + reserved
    headroom = None if required is None or maximum is None else maximum - required
    if pending:
        result = 'INPUT_INCOMPLETE'
    elif headroom < 0:
        result = 'DECLARED_CAP_EXCEEDS_LIMIT'
    else:
        result = 'WITHIN_DECLARED_LIMIT_PENDING_REVIEW'
    return {
        'scope': 'offline_db_connection_budget',
        'input_kind': inputs['input_kind'],
        'result': result,
        'pending_codes': sorted(set(pending)),
        'group_connection_caps': caps,
        'runtime_connection_cap': runtime,
        'reserved_connections': reserved,
        'required_connections': required,
        'rds_max_connections': maximum,
        'remaining_connections': headroom,
        'runtime_approval': False,
        'not_verified': ['input provenance/currentness and RDS observation',
                         'actual process/pod/engine counts and terminating connection duration',
                         'all non-App connections covered by reservation',
                         'pool timeout/recycle choice, workload performance and deployment approval'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs')
    args = parser.parse_args(argv)
    try:
        result = review(read_json(args.inputs, limit=65536))
    except Invalid as error:
        result = {'result': 'BLOCKED', 'reason_codes': [str(error)], 'runtime_approval': False}
    except Exception:
        result = {'result': 'BLOCKED', 'reason_codes': ['LOCAL_INPUT_OR_STRUCTURE_ERROR'],
                  'runtime_approval': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['result'] == 'WITHIN_DECLARED_LIMIT_PENDING_REVIEW' else 2


if __name__ == '__main__':
    sys.exit(main())
