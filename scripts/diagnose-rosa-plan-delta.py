"""Read existing plans and emit field names/change classes only; no API or Apply."""
import hashlib
import json
from pathlib import Path
import re
import sys


def canonical(value, unordered=False):
    if isinstance(value, dict):
        return {k: canonical(v, unordered) for k, v in value.items()}
    if isinstance(value, list):
        result = [canonical(v, unordered) for v in value]
        return sorted(result, key=lambda v: json.dumps(v, sort_keys=True)) if unordered else result
    return value


def change_class(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool) and a == b:
        return 'NUMERIC_REPRESENTATION_ONLY'
    if isinstance(a, list) and isinstance(b, list) and canonical(a, True) == canonical(b, True):
        return 'ARRAY_ORDER_ONLY'
    if isinstance(a, str) and isinstance(b, str):
        try:
            x, y = json.loads(a), json.loads(b)
            if x == y:
                return 'JSON_FORMAT_ONLY'
            if canonical(x, True) == canonical(y, True):
                return 'JSON_ARRAY_ORDER_ONLY'
        except (ValueError, TypeError):
            pass
        arn = r'arn:aws:sts::([0-9]{12}):assumed-role/([^/]+)/([^/]+)'
        x, y = re.fullmatch(arn, a), re.fullmatch(arn, b)
        if x and y and x.group(1, 2) == y.group(1, 2):
            return 'CALLER_SESSION_NAME_ONLY'
    return 'KNOWN_VALUE_CHANGED'


def safe_key(key):
    return key if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,80}', str(key)) else '<key>'


def differences(a, b, path='after'):
    if a is None:
        return []  # Original guard did not constrain an unknown/null value.
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        if set(a) != set(b):
            changed = set(a) ^ set(b)
            null_only = all((a if key in a else b)[key] is None for key in changed)
            result.append({'field': path, 'class': 'NULL_FIELD_PRESENCE_ONLY' if null_only else 'OBJECT_KEYS_CHANGED',
                           'changed_keys': sorted(safe_key(key) for key in changed)})
        for key in sorted(set(a) & set(b)):
            result.extend(differences(a[key], b[key], path + '.' + safe_key(key)))
        return result
    if isinstance(a, list) and isinstance(b, list):
        if a != b and canonical(a, True) == canonical(b, True):
            return [{'field': path, 'class': 'ARRAY_ORDER_ONLY', 'items': len(a)}]
        if len(a) != len(b):
            return [{'field': path, 'class': 'ARRAY_LENGTH_CHANGED', 'old_items': len(a), 'new_items': len(b)}]
        return [item for i, (x, y) in enumerate(zip(a, b)) for item in differences(x, y, path + '[' + str(i) + ']')]
    if type(a) is type(b) and a == b:
        return []
    return [{'field': path, 'class': change_class(a, b),
             'old_type': type(a).__name__, 'new_type': type(b).__name__}]


def main():
    folder = Path(sys.argv[1])
    old = json.loads((folder / 'original-plan.json').read_text())
    new = json.loads((folder / 'remaining-plan.json').read_text())
    o = {r['address']: r for r in old['resource_changes'] if r['mode'] == 'managed'}
    rows = []
    counts = {}
    for index, r in enumerate(new['resource_changes']):
        if r['mode'] != 'managed':
            continue
        actions = '/'.join(r['change']['actions'])
        counts[actions] = counts.get(actions, 0) + 1
        if r['address'] not in o:
            continue
        for difference in differences(o[r['address']]['change']['after'], r['change']['after']):
            rows.append({'resource': r['address'].split('[')[0], 'instance_number': index, **difference})
    print(json.dumps({'status': 'EXISTING_PLAN_DELTA_DIAGNOSIS', 'api_calls': 0, 'apply': 'NOT_RUN',
                      'managed_actions': counts, 'difference_count': len(rows), 'differences': rows[:100],
                      'remaining_plan_sha256': hashlib.sha256((folder / 'remaining.tfplan').read_bytes()).hexdigest(),
                      'check_status_counts': {k: sum(c.get('status') == k for c in new.get('checks', [])) for k in ('pass', 'fail', 'error', 'unknown')},
                      'values': 'NOT_EMITTED'}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, IndexError) as error:
        print(json.dumps({'status': 'LOCAL_PLAN_READ_FAILED', 'error_class': type(error).__name__,
                          'api_calls': 0, 'apply': 'NOT_RUN'}))
        raise SystemExit(1)
