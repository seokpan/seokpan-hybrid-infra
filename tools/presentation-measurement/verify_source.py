"""Check canonical source bytes and immutable vendor copies; no runtime calls."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    hashes = json.loads((root/'SHA256SUMS.json').read_text(encoding='utf-8'))
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
              and '__pycache__' not in p.parts and p.name != 'SHA256SUMS.json'}
    if set(hashes) != actual: raise RuntimeError('SOURCE_INVENTORY_MISMATCH')
    for name, wanted in hashes.items():
        raw = (root/name).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(raw).hexdigest() != wanted: raise RuntimeError('SOURCE_HASH_CHANGED:'+name)
    for name in ('cloud-business.mjs', 'measurement.mjs'):
        if (root/'vendor'/name).read_bytes().replace(b'\r\n', b'\n') != (root.parent/'cloud-measurement'/name).read_bytes().replace(b'\r\n', b'\n'):
            raise RuntimeError('APPROVED_NORMAL_ADAPTER_CHANGED:'+name)
    provenance = json.loads((root/'vendor-provenance.json').read_text())
    for name, blob in provenance['docs_blobs'].items():
        raw = (root/'vendor'/name).read_bytes().replace(b'\r\n', b'\n')
        got = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if got != blob: raise RuntimeError('ACCEPTED_BUDGET_SOURCE_CHANGED:'+name)
    print(json.dumps({'status':'PASS_LOCAL_SOURCE_MANIFEST_AND_UPSTREAM_BINDING', 'files':len(hashes), 'runtime_executed':False}))


if __name__ == '__main__': main()
