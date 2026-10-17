"""Copy and decrypt only new encrypted supplements through a pinned SSH peer."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('checkpoint', Path(__file__).with_name('prepare-rosa-checkpoint.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
need = B.need


def accepted_path(path, root):
    p, parent = PurePosixPath(path), PurePosixPath(root)
    need(p.is_absolute() and p.is_relative_to(parent) and '..' not in p.parts
         and re.fullmatch(r'[a-zA-Z0-9_./-]+', path) and path.endswith('.sops.json'),
         'CIPHERTEXT_ONLY_REMOTE_PATH_REQUIRED')
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('peer', 'peer-host-key', 'source-root', 'source-handoff', 'cache',
                 'key-file', 'key-sha256', 'sops', 'expected-hostname'):
        p.add_argument('--'+name, required=True)
    a = p.parse_args()
    os.umask(0o077)
    need(os.geteuid() == 0 and socket.gethostname().split('.')[0] == a.expected_hostname,
         'APPROVED_EXTERNAL_HOST_REQUIRED')
    need(re.fullmatch(r'[a-z][a-z0-9_-]*@[0-9.]+', a.peer), 'PINNED_SSH_PEER_REQUIRED')
    need(re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+', a.peer_host_key), 'PINNED_ED25519_KEY_REQUIRED')
    key = B.private_file(a.key_file, 65536)
    need(hashlib.sha256(key).hexdigest() == a.key_sha256, 'EXISTING_SEPARATE_KEY_BINDING_REQUIRED')
    cache = Path(a.cache)
    need(cache.is_dir() and not cache.is_symlink() and cache.stat().st_mode & 0o077 == 0,
         'EXISTING_PRIVATE_CACHE_REQUIRED')
    out = Path(tempfile.mkdtemp(prefix='supplement-copy-', dir=cache))
    known = out/'known_hosts'
    known.write_text(a.peer.split('@')[1]+' '+a.peer_host_key+'\n')
    opts = ['-o', 'UserKnownHostsFile='+str(known), '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1']
    result = {'status': 'STOP_NEW_SUPPLEMENT_COPY', 'new_archives_copied': 0,
              'new_archives_decrypted': 0, 'accepted_archives_reused': 0,
              'key_copied_again': False, 'old_archives_decrypted_again': False,
              'deletion_ready': False, 'cluster_mutations': 0, 'automatic_retry': False}
    try:
        need(a.source_handoff == a.source_root.rstrip('/')+'/rosa-preservation-handoff-latest.json', 'EXACT_HANDOFF_PATH_REQUIRED')
        def copy(source, target):
            r = subprocess.run(['scp', *opts, a.peer+':'+source, str(target)])
            need(r.returncode == 0, 'SCP_FAILED_NO_AUTOMATIC_RETRY')
            target.chmod(0o600)
        copy(a.source_handoff, out/'handoff.json')
        handoff = json.loads(B.private_file(out/'handoff.json', 262144))
        rows = handoff.get('archives', [])
        need(handoff.get('schema') == 2 and 1 <= len(rows) <= 2, 'BOUNDED_SUPPLEMENT_HANDOFF_REQUIRED')
        need({r['kind'] for r in rows} <= {'restoration-gaps', 'monitoring-snapshots'}
             and len({r['kind'] for r in rows}) == len(rows), 'EXACT_SUPPLEMENT_KINDS_REQUIRED')
        for row in rows:
            source = accepted_path(row['path'], a.source_root)
            sha = row['sha256']
            need(re.fullmatch(r'[a-f0-9]{64}', sha), 'ARCHIVE_SHA256_REQUIRED')
            archive = cache/(row['kind']+'-'+sha+'.sops.json')
            receipt = cache/('verification-'+sha+'.json')
            if receipt.is_file():
                verified = json.loads(B.private_file(receipt, 262144))
                need(verified.get('status') == 'PASS_CHECKPOINT_CONTENT_DECRYPT_AND_HASH'
                     and verified.get('archive_sha256') == sha
                     and hashlib.sha256(B.private_file(archive, B.MAX_BYTES*3)).hexdigest() == sha,
                     'ACCEPTED_COPY_CHANGED_NO_REDECRYPT')
                result['accepted_archives_reused'] += 1
                continue
            need(not archive.exists(), 'UNVERIFIED_PARTIAL_COPY_REQUIRES_RECONCILIATION')
            copy(source, archive)
            need(hashlib.sha256(B.private_file(archive, B.MAX_BYTES*3)).hexdigest() == sha, 'NEW_ARCHIVE_HASH_MISMATCH')
            result['new_archives_copied'] += 1
            verified = B.verify(argparse.Namespace(archive=str(archive), archive_sha256=sha,
                                key_file=a.key_file), out, a.sops)
            need(verified['different_hostname_observed'], 'EXTERNAL_COPY_HOST_MUST_DIFFER')
            receipt.write_bytes(B.canonical(verified))
            result['new_archives_decrypted'] += 1
        result.update(status='PASS_NEW_SUPPLEMENTS_EXTERNAL_COPY_AND_REAL_DECRYPT',
            snapshot_source_status=handoff['snapshots_status'],
            kinds=[r['kind'] for r in rows], offsite_disaster_independence='NOT_ASSERTED')
    except Exception as error:
        result['reason'] = str(error) if type(error) is RuntimeError else type(error).__name__
    (out/'receipt.json').write_bytes(B.canonical(result))
    print(json.dumps(result, indent=2))
    print('Private supplement copy directory: '+str(out))
    return 0 if result['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
