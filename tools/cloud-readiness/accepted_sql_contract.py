"""Reuse accepted SQL input contracts only. No old-cluster runner or executable entry."""
import base64
import hashlib
from urllib.parse import urlsplit, unquote


class Stop(RuntimeError): pass


def need(ok, reason):
    if not ok: raise Stop(reason)


def accepted_handoff(value):
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
    return {'host': data['SEOKPAN_DATABASE_EXPECTED_HOST'], 'database': 'stone_game',
            'accounts': accounts, 'ca_sha256': hashlib.sha256(ca.encode()).hexdigest(),
            'final_import_compare_and_schema_handoff_accepted': handoff['final_import_compare_and_schema_handoff_accepted'],
            'source_writer_hold_accepted': handoff['source_writer_hold_accepted']}
