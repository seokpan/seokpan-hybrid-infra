"""Reuse accepted SQL input contracts only. No old-cluster runner or executable entry."""
import base64
import hashlib
from urllib.parse import urlsplit, unquote


class Stop(RuntimeError): pass


def need(ok, reason):
    if not ok: raise Stop(reason)


def fresh_target_handoff(value):
    need(value.get('data_mode') == 'fresh_test' and value.get('accepted_by') == 'tjung03'
         and value.get('fresh_test_target_accepted') is True and value.get('legacy_targets_excluded') is True,
         'FRESH_TARGET_ISOLATION_ACCEPTANCE_REQUIRED')
    need(value.get('target_database_name') == 'stone_game'
         and all(isinstance(value.get(k), str) and value[k].strip() for k in
                 ('target_database_host', 'target_redis_host', 'fresh_target_evidence_ref')),
         'FRESH_TARGET_REFERENCE_REQUIRED')
    need(not value.get('final_import_compare_and_schema_handoff_accepted')
         and not value.get('source_writer_hold_accepted'), 'FRESH_MODE_MUST_NOT_ASSERT_LEGACY_IMPORT_OR_STOP')
    return True


def handoff_binding(value):
    if value.get('data_mode') != 'fresh_test': return value
    fresh_target_handoff(value)
    # Data Pod 준비 뒤 계정/Schema 근거만 추가한다. 대상/보호 입력 변경은 허용하지 않는다.
    return {k: v for k, v in value.items() if k not in
            ('purpose_accounts_evidence_ref', 'schema_prepared_evidence_ref', 'reported_schema_revision')}


def accepted_handoff(value):
    mode = value.get('data_mode', 'migrated')
    need(mode in ('migrated', 'fresh_test'), 'EXPLICIT_KNOWN_DATA_MODE_REQUIRED')
    if mode == 'fresh_test':
        fresh_target_handoff(value)
        missing = []
        for name in ('fresh_test_target_accepted', 'legacy_targets_excluded'):
            if value.get(name) is not True: missing.append(name)
        for name in ('target_database_host', 'target_redis_host', 'fresh_target_evidence_ref',
                     'purpose_accounts_evidence_ref', 'schema_prepared_evidence_ref'):
            if not isinstance(value.get(name), str) or not value[name].strip(): missing.append(name)
        if value.get('target_database_name') != 'stone_game': missing.append('target_database_name')
        if value.get('accepted_by') != 'tjung03': missing.append('accepted_by')
        if value.get('reported_schema_revision') != '20260902_0002': missing.append('reported_schema_revision')
        need(not missing, 'PENDING_FRESH_TEST_HANDOFF_FIELDS:' + ','.join(missing))
        need(not value.get('final_import_compare_and_schema_handoff_accepted')
             and not value.get('source_writer_hold_accepted'), 'FRESH_MODE_MUST_NOT_ASSERT_LEGACY_IMPORT_OR_STOP')
        return True
    missing = []
    for name in ('final_import_compare_and_schema_handoff_accepted', 'source_writer_hold_accepted'):
        if value.get(name) is not True: missing.append(name)
    for name in ('final_import_evidence_ref', 'source_writer_hold_evidence_ref'):
        if not isinstance(value.get(name), str) or not value[name].strip(): missing.append(name)
    if value.get('accepted_by') != 'tjung03': missing.append('accepted_by')
    if value.get('reported_schema_revision') != '20260902_0002': missing.append('reported_schema_revision')
    need(not missing, 'PENDING_ACCEPTED_HANDOFF_FIELDS:' + ','.join(missing))
    # These are accepted handoff assertions, not independent execution proof.
    return True


def runtime_payload(bundle, secret, ca, handoff):
    accepted_handoff(handoff)
    maps = [o for o in bundle['items'] if o.get('kind') == 'ConfigMap'
            and 'SEOKPAN_DATABASE_EXPECTED_HOST' in o.get('data', {})]
    need(len(maps) == 1, 'ONE_ACCEPTED_DATABASE_CONTRACT_REQUIRED')
    data = maps[0]['data']
    need(data.get('SEOKPAN_DATABASE_EXPECTED_NAME') == 'stone_game'
         and data.get('SEOKPAN_DATABASE_EXPECTED_PORT') == '3306', 'EXPECTED_DATABASE_CONTRACT_REQUIRED')
    accounts = []
    for key, user in (('SEOKPAN_IDENTITY_DATABASE_URL', 'identity_svc'),
                      ('SEOKPAN_GAME_DATABASE_URL', 'game_svc')):
        url = urlsplit(base64.b64decode(secret['data'][key], validate=True).decode())
        need(url.scheme == 'mysql+asyncmy' and url.hostname == data['SEOKPAN_DATABASE_EXPECTED_HOST']
             and url.port == 3306 and unquote(url.path.lstrip('/')) == 'stone_game'
             and unquote(url.username or '') == user and bool(url.password) and not url.fragment,
             'PROTECTED_RUNTIME_URL_CONTRACT_MISMATCH')
        accounts.append({'user': user, 'password': unquote(url.password)})
    result = {'host': data['SEOKPAN_DATABASE_EXPECTED_HOST'], 'database': 'stone_game',
              'accounts': accounts, 'ca_sha256': hashlib.sha256(ca.encode()).hexdigest()}
    if handoff.get('data_mode') == 'fresh_test':
        need(handoff['target_database_host'] == result['host']
             and handoff['target_redis_host'] == data.get('SEOKPAN_REDIS_EXPECTED_HOST'),
             'FRESH_TARGET_MUST_MATCH_BOUND_DATABASE_AND_REDIS')
        result.update({k: handoff[k] for k in ('data_mode', 'fresh_test_target_accepted',
            'legacy_targets_excluded', 'target_database_host', 'target_redis_host',
            'reported_schema_revision', 'fresh_target_evidence_ref',
            'purpose_accounts_evidence_ref', 'schema_prepared_evidence_ref')})
    else:
        result.update(final_import_compare_and_schema_handoff_accepted=handoff['final_import_compare_and_schema_handoff_accepted'],
                      source_writer_hold_accepted=handoff['source_writer_hold_accepted'])
    return result

