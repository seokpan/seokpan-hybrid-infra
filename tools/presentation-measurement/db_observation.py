"""Existing MariaDB TLS observer path, bounded read-only samples + existing budget checker.

No credentials in argv/output. Only --execute samples. This creates no Pod,
grants no privileges, and cannot infer full process visibility from an empty list.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import socket
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from timeline import need, sha, canonical
from stage_clock import read, save

SQL = """SHOW SESSION STATUS LIKE 'Ssl_cipher';
SELECT 'GLOBAL_LIMIT',@@GLOBAL.max_connections,@@GLOBAL.max_user_connections;
SHOW GLOBAL STATUS LIKE 'Threads_connected';
SHOW GRANTS FOR CURRENT_USER;
SELECT 'ACCOUNT',USER,COUNT(*) FROM INFORMATION_SCHEMA.PROCESSLIST GROUP BY USER;
"""


def parse(text, expected_accounts):
    rows = [line.split('\t') for line in text.splitlines()]
    tls = next((r[1] for r in rows if r[0] == 'Ssl_cipher' and len(r) == 2), None)
    limit = next((r for r in rows if r[0] == 'GLOBAL_LIMIT' and len(r) == 3), None)
    count = next((r[1] for r in rows if r[0] == 'Threads_connected' and len(r) == 2), None)
    need(tls and limit and count is not None, 'TLS_AND_CURRENT_GLOBAL_COUNTS_REQUIRED')
    grants = [r[0].upper() for r in rows if len(r) == 1 and r[0].upper().startswith('GRANT ')]
    visible = any(re.search(r'\b(?:ALL PRIVILEGES|PROCESS)\b.*\bON\s+\*\.\*(?:\s|$)', g) for g in grants)
    # A read-only DB account without PROCESS sees a subset, not a server-wide total.
    account_rows = {r[1]: int(r[2]) for r in rows if r[0] == 'ACCOUNT' and len(r) == 3}
    return {'global_limit': int(limit[1]), 'global_max_user_connections': int(limit[2]),
            'connections_total': int(count), 'observer_connection_included': True,
            'per_account': {a: account_rows.get(a, 0) if visible else None for a in expected_accounts},
            'per_account_visibility': 'ALL_PROCESS_PRIVILEGE_CONFIRMED' if visible else 'NOT_CONFIRMED_VALUES_NULL',
            'tls_cipher_present': True, 'session_grants_exported': False}


def observed_summary(rows):
    good = [r for r in rows if r.get('sample')]
    if not good:
        return {'status': 'NO_VALID_DB_SAMPLES', 'max_connections_observed': None, 'minimum_observed_headroom': None}
    values = [r['sample']['connections_total'] for r in good]
    gaps = [(b['monotonic_ns']-a['monotonic_ns'])/1e9 for a, b in zip(good, good[1:])]
    limits = sorted({r['sample']['global_limit'] for r in good})
    return {'status': 'COLLECTED_DB_SAMPLES_NOT_CONTINUOUS_PEAK', 'valid_samples': len(good),
            'failed_samples': len(rows)-len(good), 'global_limits_observed': limits,
            'max_connections_observed': max(values),
            'minimum_observed_headroom': min(r['sample']['global_limit']-r['sample']['connections_total'] for r in good),
            'reservation_subtracted_again': False, 'actual_intervals_seconds': gaps,
            'unobserved_subinterval_peaks': True,
            'maximum_observed_per_account': {a: max([r['sample']['per_account'][a] for r in good
                if r['sample']['per_account'].get(a) is not None], default=None) for a in good[0]['sample']['per_account']},
            'own_observer_connection_included': True, 'counts_not_single_transaction_atomic': True}


def budget_report(inputs):
    vendor = Path(__file__).parent/'vendor'
    sys.path.insert(0, str(vendor))
    spec = importlib.util.spec_from_file_location('accepted_budget_checker', vendor/'db_connection_budget.py')
    m = importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    # The upstream checker requires one homogeneous configuration per group.
    # Mixed old/new: caller supplies component-wise conservative maxima, not an average.
    return m.review(inputs)


def account_budget(inputs, limits):
    totals = {}
    for name in ('identity', 'game'):
        parts = []
        for group in inputs['pod_groups'].values():
            e = group['engines'][name]
            values = [group['pods'], group['processes_per_pod'], e['pool_size'], e['max_overflow']]
            parts.append(None if any(v is None for v in values) else values[0]*values[1]*(values[2]+values[3]))
        cap = None if any(v is None for v in parts) else sum(parts)
        account = name+'_svc'
        current = limits[account]
        need(current.get('evidence_ref'), 'ACTUAL_ACCOUNT_LIMIT_REFERENCE_REQUIRED')
        user, global_user = current['max_user_connections'], current['global_max_user_connections']
        need(type(user) is int and user >= 0 and type(global_user) is int and global_user >= 0, 'ACTUAL_ACCOUNT_LIMITS_REQUIRED')
        effective = user if user > 0 else global_user if global_user > 0 else None
        totals[account] = {'configured_app_cap': cap, 'account_limit': user, 'global_user_limit': global_user,
            'effective_additional_per_account_cap': effective,
            'meaning_when_both_zero': 'NO_ADDITIONAL_ACCOUNT_CAP_GLOBAL_DB_LIMIT_STILL_APPLIES',
            'within_account_limit': None if cap is None else effective is None or cap <= effective,
            'evidence_ref': current['evidence_ref']}
    return totals


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['budget', 'sample', 'summary'])
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--account-limits', type=Path)
    a = p.parse_args();os.umask(0o077)
    try:
        c = read(a.config)
        if a.action == 'budget':
            result = budget_report(c)
            if a.account_limits:
                result['per_account'] = account_budget(c, read(a.account_limits))
                if any(x['within_account_limit'] is False for x in result['per_account'].values()): result['result'] = 'ACCOUNT_CAP_EXCEEDS_LIMIT'
                elif any(x['within_account_limit'] is None for x in result['per_account'].values()): result['result'] = 'INPUT_INCOMPLETE'
            save(a.out, result)
            print(json.dumps(result));return 0 if result['result'] == 'WITHIN_DECLARED_LIMIT_PENDING_REVIEW' else 2
        if a.action == 'summary':
            result = observed_summary([json.loads(x) for x in a.out.read_text().splitlines()])
            result.update(requested_interval_seconds=c['interval_seconds'], requested_duration_seconds=c['duration_seconds'])
            print(json.dumps(result, indent=2));return 0 if result.get('valid_samples', 0) > 0 else 2
        need(c.get('target_reference') and c.get('binding') and c.get('credential_file') and c.get('ca_file'), 'CURRENT_OBSERVER_INPUTS_REQUIRED')
        need(2 <= c['interval_seconds'] <= 60 and 1 <= c['duration_seconds'] <= 900, 'BOUNDED_DB_SAMPLE_PROFILE_REQUIRED')
        need(set(c['accounts']) == {'identity_svc', 'game_svc'}, 'TWO_KNOWN_ACCOUNT_NAMES_REQUIRED')
        if not a.execute:
            print(json.dumps({'status': 'PREPARED_READ_ONLY_DB_OBSERVER_NO_EXECUTION', 'resource_mutations': 0, 'sql_calls': 0}));return 0
        credential = Path(c['credential_file'])
        need(credential.is_file() and not credential.is_symlink(), 'PRIVATE_EXISTING_MARIADB_DEFAULTS_FILE_REQUIRED')
        if os.name == 'posix':
            s = credential.stat()
            need(s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) & 0o077 == 0, 'PRIVATE_OWNED_CREDENTIAL_FILE_REQUIRED')
        text = credential.read_text()
        need('[client]' in text and not re.search(r'^\s*!include', text, re.M), 'SINGLE_EXISTING_CLIENT_CONFIG_REQUIRED')
        need(Path(c['ca_file']).is_file(), 'CURRENT_CA_FILE_REQUIRED')
        need(not a.out.exists(), 'NEW_DB_OBSERVATION_FILE_REQUIRED')
        end = min(time.monotonic()+c['duration_seconds'], time.monotonic()+max(0, dt.datetime.fromisoformat(c['not_after'].replace('Z', '+00:00')).timestamp()-time.time()))
        need(end > time.monotonic(), 'CURRENT_WINDOW_REQUIRED')
        rows = []
        while time.monotonic() < end:
            started = time.monotonic_ns()
            row = {'binding': c['binding'], 'target_reference_sha256': sha(c['target_reference']),
                   'collector_host': socket.gethostname(),
                   'monotonic_ns': started, 'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'sample': None}
            try:
                r = subprocess.run([c['mariadb'], '--defaults-extra-file='+str(credential),
                    '--host='+c['database_host'], '--port=3306', '--protocol=TCP', '--ssl', '--ssl-verify-server-cert',
                    '--ssl-ca='+c['ca_file'], '--connect-timeout=5', '--batch', '--skip-column-names', '--execute='+SQL],
                    capture_output=True, text=True, timeout=min(15,max(1,end-time.monotonic())))
                need(r.returncode == 0, 'DB_READ_DENIED_OR_FAILED')
                row['sample'] = parse(r.stdout, c['accounts'])
            except Exception:
                row['error'] = 'READ_OR_PARSE_FAILED_RAW_NOT_EXPORTED'
            row['finished_monotonic_ns'] = time.monotonic_ns()
            rows.append(row)
            fd = os.open(a.out, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'ab') as f: f.write(canonical(row)+b'\n')
            # Future periodic samples are not mutation retries. Gaps/errors remain explicit.
            time.sleep(max(0, min(end-time.monotonic(), c['interval_seconds']-(time.monotonic_ns()-started)/1e9)))
        result = observed_summary(rows)
        result.update(requested_interval_seconds=c['interval_seconds'], requested_duration_seconds=c['duration_seconds'])
        print(json.dumps(result, indent=2));return 0 if result.get('valid_samples', 0) > 0 else 2
    except Exception as e:
        print(json.dumps({'status': 'STOP_DB_OBSERVER', 'reason': str(e) if type(e) is RuntimeError else type(e).__name__,
                          'automatic_mutation_retry': False, 'raw_output_exported': False}));return 2


if __name__ == '__main__': raise SystemExit(main())
