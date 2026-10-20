"""완료된 시험의 공개 사본만 고정 명령으로 전달합니다."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

RUN = Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62/controller-run-bundle-K2Tx7HgB'
COMMANDS = {'b-hpa-evidence-summary-v1', 'b-hpa-evidence-archive-v1',
            'b-hpa-publication-proof-v1'}

def need(ok, code):
    if not ok:
        raise RuntimeError(code)

def raw(path, maximum=25165824):
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
             and info.st_size <= maximum, 'OWNED_BOUNDED_PUBLIC_EVIDENCE_REQUIRED')
        return stream.read(maximum+1)

def read(path):
    return json.loads(raw(path, 1048576))

def route(command):
    need(command in COMMANDS, 'EXACT_PUBLIC_EVIDENCE_COMMAND_REQUIRED')
    pointer = read(RUN/'hpa-active-time-copy.json')
    directory = Path(pointer['directory'])/'final-closeout'
    need(directory.resolve().is_relative_to((RUN/'hpa-20261020-124957').resolve())
         and not directory.is_symlink(), 'EXACT_RUN_CLOSEOUT_REQUIRED')
    receipt = read(directory/'receipt.json')
    need(receipt['status'] == 'PASS_RUN_STOPPED_RESTORED_EVIDENCE_PRESERVED_NOT_TEST_VERDICT'
         and receipt['restoration_verified'] is True, 'POST_TEST_CLOSEOUT_REQUIRED')
    archive = directory/'public-evidence.zip'
    data = raw(archive)
    digest = hashlib.sha256(data).hexdigest()
    need(digest == receipt['public_archive_sha256'], 'PUBLIC_ARCHIVE_HASH_CHANGED')
    if command == 'b-hpa-evidence-summary-v1':
        result = {'status':'PASS_PUBLIC_HPA_EVIDENCE_AVAILABLE',
                  'public_archive_sha256':digest, 'summary':read(directory/'public/summary.json')}
    elif command == 'b-hpa-evidence-archive-v1':
        result = {'status':'PASS_PUBLIC_HPA_ARCHIVE_ONLY',
                  'sha256':digest, 'base64':base64.b64encode(data).decode('ascii')}
    else:
        value = json.loads(sys.stdin.buffer.read(16385))
        need(set(value) == {'status','run_id','public_archive_sha256','urls'}
             and value['status'] == 'PASS_GITHUB_PUBLIC_EVIDENCE_SAVED'
             and value['run_id'] == receipt['run_id']
             and value['public_archive_sha256'] == digest,
             'MATCHING_PUBLICATION_PROOF_REQUIRED')
        need(isinstance(value['urls'], list) and 1 <= len(value['urls']) <= 8
             and all(isinstance(u,str) and re.fullmatch(
                 r'https://github\.com/seokpan/seokpan-hybrid-(?:infra|docs)/[A-Za-z0-9_./#-]+', u)
                     for u in value['urls']), 'EXACT_PROJECT_PUBLICATION_URL_REQUIRED')
        target = directory/'github-publication.json'
        if target.exists():
            need(read(target) == value, 'EXISTING_PUBLICATION_PROOF_PRESERVED')
        else:
            fd = os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump(value,stream)
        result = {'status':'PASS_PUBLICATION_REFERENCE_RECORDED', 'sha256':digest}
    print(json.dumps(result))
    return 0

