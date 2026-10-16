"""Encrypted project checkpoint and portable verification; no resource mutations."""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import urllib.request

MAX_BYTES = 96 * 1024 * 1024
SOPS_SHA = "e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b"

def need(ok, code):
    if not ok:
        raise RuntimeError(code)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def private_file(path, limit=MAX_BYTES):
    path = Path(path)
    need(not path.is_symlink(), "SYMLINK_INPUT_REJECTED")
    with path.open("rb") as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_size <= limit, "REGULAR_BOUNDED_INPUT_REQUIRED")
        if os.name == "posix":
            need(s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) & 0o077 == 0, "PRIVATE_OWNED_INPUT_REQUIRED")
        return f.read(limit + 1)

def age_recipient(raw):
    """Read public recipients from SOPS metadata, never from payload fields."""
    text = raw.decode("utf-8-sig")
    if text.lstrip().startswith("{"):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                need(key not in result, "DUPLICATE_SOPS_JSON_KEY")
                result[key] = value
            return result
        document = json.loads(text, object_pairs_hook=unique)
        metadata = document.get("sops") if isinstance(document, dict) else None
        need(isinstance(metadata, dict) and not metadata.get("key_groups"), "SUPPORTED_SOPS_METADATA_REQUIRED")
        rows = metadata.get("age")
        need(isinstance(rows, list) and all(isinstance(x, dict) for x in rows), "SOPS_AGE_ROWS_REQUIRED")
        values = [x.get("recipient") for x in rows]
        form = "SOPS_JSON"
    else:
        # Standard SOPS block YAML; no general YAML loader or tag evaluation.
        lines = text.splitlines()
        starts = [i for i, line in enumerate(lines) if re.fullmatch(r'''(?:sops|"sops"|'sops'):[ \t]*(?:#.*)?''', line)]
        need(len(starts) == 1, "SUPPORTED_SOPS_YAML_METADATA_REQUIRED")
        block = []
        for line in lines[starts[0]+1:]:
            if line.strip() and not line.startswith(" ") and not line.lstrip().startswith("#"):
                break
            block.append(line)
        need(not any(re.match(r"^\s+key_groups:", line) for line in block), "SUPPORTED_SOPS_METADATA_REQUIRED")
        ages = [(i, len(m[1])) for i, line in enumerate(block)
                if (m := re.fullmatch(r'''( +)(?:age|"age"|'age'):[ \t]*''', line))]
        need(len(ages) == 1, "SOPS_AGE_ROWS_REQUIRED")
        start, indent = ages[0]
        section = []
        for line in block[start+1:]:
            width = len(line)-len(line.lstrip(" "))
            if line.strip() and not line.lstrip().startswith("#") and (width < indent or (width == indent and not line.lstrip().startswith("- "))):
                break
            section.append(line)
        values = []
        for line in section:
            m = re.fullmatch(r'''\s+(?:-\s+)?(?:recipient|"recipient"|'recipient'):\s*(?:"(age1[0-9a-z]+)"|'(age1[0-9a-z]+)'|(age1[0-9a-z]+))[ \t]*(?:#.*)?''', line)
            if m: values.append(next(x for x in m.groups() if x is not None))
        form = "SOPS_BLOCK_YAML"
    need(values and all(isinstance(x, str) and re.fullmatch(r"age1[0-9a-z]+", x) for x in values)
         and len(set(values)) == 1, "ONE_EXISTING_AGE_RECIPIENT_REQUIRED")
    return values[0], form

def recipient_shape(raw):
    """Counts/styles only. No recipient, ciphertext, key or payload is returned."""
    text = raw.decode("utf-8-sig")
    result = {"format":"JSON" if text.lstrip().startswith("{") else "YAML_TEXT_STRUCTURE",
              "duplicate_json_keys":0, "recipient_fields":0, "scalar_styles":{}}
    candidates = []
    if result["format"] == "JSON":
        def pairs(rows):
            obj = {}
            for key, value in rows:
                if key in obj: result["duplicate_json_keys"] += 1
                obj[key] = value
            return obj
        doc = json.loads(text, object_pairs_hook=pairs)
        meta = doc.get("sops") if isinstance(doc, dict) else None
        result["sops_mapping_present"] = isinstance(meta, dict)
        age = meta.get("age") if isinstance(meta, dict) else None
        result["age_container_type"] = type(age).__name__
        result["key_groups_present"] = bool(meta.get("key_groups")) if isinstance(meta, dict) else False
        result["age_entries"] = len(age) if isinstance(age, list) else 0
        for row in age if isinstance(age, list) else []:
            if isinstance(row, dict) and "recipient" in row:
                result["recipient_fields"] += 1
                value = row["recipient"]
                style = "string" if isinstance(value, str) else "non_string"
                result["scalar_styles"][style] = result["scalar_styles"].get(style,0)+1
                if isinstance(value, str): candidates.append(value)
    else:
        lines = text.splitlines()
        starts = [i for i,line in enumerate(lines) if re.fullmatch(r'''(?:sops|"sops"|'sops'):[ \t]*(?:#.*)?''',line)]
        result["sops_headers"] = len(starts)
        block = []
        if len(starts) == 1:
            for line in lines[starts[0]+1:]:
                if line.strip() and not line.startswith(" ") and not line.lstrip().startswith("#"): break
                block.append(line)
        result["age_header_indents"] = [len(m[1]) for line in block
            if (m := re.fullmatch(r'''( +)(?:age|"age"|'age'):[ \t]*''',line))]
        result["key_groups_present"] = any(re.match(r'''\s+(?:key_groups|"key_groups"|'key_groups'):''',line) for line in block)
        result["recipient_field_indents"] = []
        for line in block:
            match = re.fullmatch(r'''( +)(?:-\s+)?(?:recipient|"recipient"|'recipient'):[ \t]*(.*)''',line)
            if not match: continue
            result["recipient_fields"] += 1
            result["recipient_field_indents"].append(len(match[1]))
            value = match[2].split(" #",1)[0].strip()
            style = "empty" if not value else "block" if value[0] in "|>" else "quoted" if value[0] in "\"'" else "plain"
            result["scalar_styles"][style] = result["scalar_styles"].get(style,0)+1
            if style == "quoted" and len(value)>1 and value[-1]==value[0]: value=value[1:-1]
            candidates.append(value)
    valid = [value for value in candidates if re.fullmatch(r"age1[0-9a-z]+",value)]
    result.update(age_syntax_scalars=len(valid), unique_age_syntax_scalars=len(set(valid)),
                  semantic_yaml_validation=False, crypto_validation=False)
    try:
        age_recipient(raw)
        result["current_single_recipient_parser_accepts"] = True
    except Exception as error:
        result.update(current_single_recipient_parser_accepts=False,
            parser_stop=str(error) if type(error) is RuntimeError else type(error).__name__)
    return result

def diagnose(args):
    c = json.loads(private_file(args.contract,262144))
    need(c.get("schema") == 1, "CHECKPOINT_CONTRACT_REQUIRED")
    raw = private_file(c["recipient_source"],262144)
    need(hashlib.sha256(raw).hexdigest() == c["recipient_source_sha256"], "RECIPIENT_SOURCE_HASH_MISMATCH")
    return {"status":"COLLECTED_RECIPIENT_METADATA_ONLY_NOT_BACKUP", "deletion_ready":False,
            "cipher_sha256_matches":True, "recipient_structure":recipient_shape(raw),
            "sops_decrypt_invocations":0, "backup_retried":False}

def validate_resume(path):
    previous = Path(path)
    result = json.loads(private_file(previous/"receipt.json", 262144))
    need(result.get("status") == "STOP_CHECKPOINT" and result.get("reason") == "ONE_EXISTING_AGE_RECIPIENT_REQUIRED"
         and result.get("resource_mutations") == 0 and not result.get("sync_started")
         and not (previous/"checkpoint.sops.json").exists(), "UNENCRYPTED_RECIPIENT_STOP_ONLY_RESUME_REQUIRED")
    return str(previous)

def sops_binary(args, out):
    if args.sops:
        need(Path(args.sops).is_file(), "EXISTING_SOPS_REQUIRED")
        return str(Path(args.sops).resolve())
    found = shutil.which("sops")
    if found:
        return found
    need(platform.system() == "Linux" and platform.machine() in ("x86_64", "amd64"), "EXISTING_SOPS_REQUIRED_ON_THIS_PLATFORM")
    url = "https://github.com/getsops/sops/releases/download/v3.13.3/sops-v3.13.3.linux.amd64"
    with urllib.request.urlopen(url, timeout=40) as response:
        data = response.read(67108865)
    need(len(data) <= 67108864 and hashlib.sha256(data).hexdigest() == SOPS_SHA, "SOPS_RELEASE_BINARY_HASH_MISMATCH")
    path = out / "sops"
    path.write_bytes(data)
    path.chmod(0o700)
    return str(path)

def crypt(binary, data, decrypt=False, recipient=None, key_file=None):
    argv = [binary, "--decrypt" if decrypt else "--encrypt", "--input-type", "json", "--output-type", "json"]
    env = os.environ.copy()
    if decrypt:
        private_file(key_file, 65536)
        env.pop("SOPS_AGE_KEY", None)
        env["SOPS_AGE_KEY_FILE"] = str(Path(key_file).resolve())
    else:
        argv += ["--age", recipient]
    encrypted_temp = None
    if os.name != "posix":
        need(decrypt, "CHECKPOINT_CAPTURE_REQUIRES_LINUX_CONTROLLER")
        with tempfile.NamedTemporaryFile(prefix="sops-cipher-only-", suffix=".json", delete=False) as f:
            f.write(data)
            encrypted_temp = Path(f.name)
    try:
        p = subprocess.run(argv + [str(encrypted_temp) if encrypted_temp else "/dev/stdin"],
                           input=None if encrypted_temp else data, capture_output=True, env=env, timeout=120)
    finally:
        if encrypted_temp: encrypted_temp.unlink(missing_ok=True)
    need(p.returncode == 0, "CHECKPOINT_DECRYPT_FAILED" if decrypt else "CHECKPOINT_ENCRYPT_FAILED")
    return p.stdout

def collect_files(roots, excluded):
    rows, total = [], 0
    for root in roots:
        root = Path(root).resolve()
        need(root.is_dir(), "PROJECT_INPUT_ROOT_MISSING")
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in ("providers", ".git", "node_modules", ".venv", "__pycache__")
                       and not (Path(folder)/d).is_symlink() and (Path(folder)/d).resolve() not in excluded]
            for name in sorted(files):
                p = Path(folder)/name
                if p.is_symlink() or name in ("identity-to-deliver.txt", "keys.txt"):
                    continue
                if p.suffix not in (".json", ".yaml", ".yml", ".tf", ".hcl", ".sh", ".py", ".md", ".txt", ".log", ".tfplan", ".plan", ".tfstate", ".backup"):
                    continue
                need(p.stat().st_size <= MAX_BYTES, "PROJECT_FILE_TOO_LARGE_NO_SILENT_TRUNCATION")
                raw = p.read_bytes()
                if re.search(rb"AGE-SECRET-KEY-1[0-9A-Z]{40,100}", raw):
                    continue
                total += len(raw)
                need(total <= MAX_BYTES, "CHECKPOINT_TOO_LARGE_SELECT_INPUTS_EXPLICITLY")
                rows.append({"root": str(root), "path": str(p.relative_to(root)),
                             "sha256": hashlib.sha256(raw).hexdigest(), "data": base64.b64encode(raw).decode()})
    return rows

def backup(args, out, binary, audit=None):
    audit = audit if audit is not None else {}
    c = json.loads(private_file(args.contract, 262144))
    need(c.get("schema") == 1 and c.get("expected_api", "").startswith("https://"), "CHECKPOINT_CONTRACT_REQUIRED")
    audit["phase"] = "RECIPIENT_PREFLIGHT"
    cipher_raw = private_file(c["recipient_source"], 262144)
    need(hashlib.sha256(cipher_raw).hexdigest() == c["recipient_source_sha256"], "RECIPIENT_SOURCE_HASH_MISMATCH")
    recipient, form = age_recipient(cipher_raw)
    audit.update(recipient_metadata_format=form, unique_age_recipients=1)
    cloud = c["cloud_namespace"]
    previous = Path(c["previous_inventory"])
    receipt = json.loads(private_file(previous/"receipt.json", 262144))
    counts = c.get("inventory_counts", {})
    need(isinstance(counts.get("pvc"), int) and isinstance(counts.get("cloud_secrets"), int)
         and receipt.get("pvc_count") == counts["pvc"] and receipt.get("secret_inventory_counts", {}).get(cloud) == counts["cloud_secrets"],
         "ACCEPTED_PREVIOUS_INVENTORY_REQUIRED")
    private_file(args.kubeconfig, 262144)
    audit["phase"] = "CONFIG_CAPTURE"
    errors = []
    def get(resource, name=None, namespace=None):
        cmd = ["oc", "--kubeconfig", args.kubeconfig, "--request-timeout=20s", "get", resource]
        if name: cmd.append(name)
        if namespace: cmd += ["-n", namespace]
        p = subprocess.run(cmd + ["-o", "json"], capture_output=True, timeout=35)
        if p.returncode:
            if any(x in p.stderr.lower() for x in (b"unauthorized", b"must be logged in", b"provide credentials")):
                raise RuntimeError("CHECKPOINT_AUTH_REQUIRED_NO_RETRY")
            errors.append({"resource": resource, "namespace": namespace, "result": "READ_DENIED_OR_FAILED"})
            return None
        return json.loads(p.stdout)
    p = subprocess.run(["oc", "--kubeconfig", args.kubeconfig, "config", "view", "--minify", "-o", "json"], capture_output=True, timeout=20)
    need(p.returncode == 0, "CHECKPOINT_LOCAL_CONTEXT_REQUIRED")
    cluster = json.loads(p.stdout)["clusters"][0]["cluster"]
    need(cluster["server"].rstrip("/") == c["expected_api"].rstrip("/") and not cluster.get("insecure-skip-tls-verify"), "CHECKPOINT_TARGET_MISMATCH")
    snapshots = []
    argo = c["argo_namespace"]
    for resource in ("namespace",):
        obj = get(resource, cloud)
        if obj: snapshots.append(obj)
    for resource in ("secrets", "configmaps", "serviceaccounts", "roles", "rolebindings", "networkpolicies", "deployments", "statefulsets", "services", "routes"):
        obj = get(resource, namespace=cloud)
        if obj is not None:
            for item in obj.get("items", []):
                if item.get("type") != "kubernetes.io/service-account-token": snapshots.append(item)
    # A list-Secrets denial in the Argo namespace is not retried.
    for resource, name in [("argocd", c["argo_name"])] + [("appproject", x) for x in c["projects"]] + [("application", x) for x in c["applications"]]:
        obj = get(resource, name, argo)
        if obj: snapshots.append(obj)
    oauth = get("oauth", "cluster")
    if oauth:
        snapshots.append(oauth)
        for provider in oauth.get("spec", {}).get("identityProviders", []):
            if provider.get("name") not in c["identity_providers"]: continue
            name = provider.get("github", {}).get("clientSecret", {}).get("name")
            if name:
                obj = get("secret", name, "openshift-config")
                if obj: snapshots.append(obj)
    for name in c["groups"]:
        obj = get("group", name)
        if obj: snapshots.append(obj)
    files = collect_files(c["local_roots"], {out.resolve()})
    expected = json.loads(private_file(previous/(cloud+"-secret-inventory.json"), 262144))
    old = {x["name"]: x["uid"] for x in expected if x.get("type") != "kubernetes.io/service-account-token"}
    actual = {x["metadata"]["name"]: x["metadata"]["uid"] for x in snapshots if x.get("kind") == "Secret" and x["metadata"].get("namespace") == cloud}
    need(old == actual, "CLOUD_SECRET_INVENTORY_CHANGED_NO_OVERWRITE")
    bundle = {"schema": 1, "origin_host": socket.gethostname(), "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "contract": c, "snapshots": snapshots, "files": files, "read_errors": errors,
              "pvc_inventory": json.loads(private_file(previous/"pvc-inventory.json", 262144)),
              "remote_state_current": False, "volume_data_backed_up": False,
              "gitops_secret_gap": "OPERATOR_REGENERATION_OR_OWNER_EXPORT_ACCEPTANCE_REQUIRED"}
    raw = canonical(bundle)
    audit.update(phase="ENCRYPT", encryption_attempted=True)
    encrypted = crypt(binary, raw, recipient=recipient)
    archive = out/"checkpoint.sops.json"
    archive.write_bytes(encrypted)
    audit.update(phase="LOCAL_DECRYPT_AND_COMPARE", archive_written=True)
    decrypted = json.loads(crypt(binary, encrypted, decrypt=True, key_file=args.key_file))
    need(canonical(decrypted) == raw, "LOCAL_CHECKPOINT_ROUNDTRIP_MISMATCH")
    audit["phase"] = "LOCAL_CHECKPOINT_COMPLETE"
    return {"status": "PARTIAL_ENCRYPTED_CHECKPOINT" if errors else "PASS_CONFIG_CHECKPOINT_LOCAL_ROUNDTRIP",
            "archive": str(archive), "archive_sha256": hashlib.sha256(encrypted).hexdigest(),
            "snapshot_objects": len(snapshots), "project_files": len(files), "read_errors": errors,
            "local_decrypt_verified": True, "independent_copy_verified": False, "deletion_ready": False,
            "pvc_disposition_required": [{"namespace":x["namespace"], "name":x["name"], "phase":x.get("phase")} for x in bundle["pvc_inventory"]],
            "local_plan_input_candidates": json.loads(private_file(previous/"local-preservation-file-inventory.json",262144)),
            "still_required": ["CURRENT_REMOTE_STATE", "PVC_DATA_DISPOSITION", "GITOPS_SECRET_GAP_ACCEPTANCE", "INDEPENDENT_COPY_AND_DECRYPT", "REVIEWED_DELETE_PLAN", "EXPLICIT_USER_DELETE_CONFIRMATION"]}

def verify(args, out, binary):
    encrypted = private_file(args.archive, MAX_BYTES*3)
    need(hashlib.sha256(encrypted).hexdigest() == args.archive_sha256, "ARCHIVE_HASH_MISMATCH")
    b = json.loads(crypt(binary, encrypted, decrypt=True, key_file=args.key_file))
    need(b.get("schema") == 1 and isinstance(b.get("snapshots"), list), "CHECKPOINT_SCHEMA_REQUIRED")
    for row in b["files"]:
        need(hashlib.sha256(base64.b64decode(row["data"], validate=True)).hexdigest() == row["sha256"], "INNER_FILE_HASH_MISMATCH")
    return {"status": "PASS_CHECKPOINT_CONTENT_DECRYPT_AND_HASH", "archive_sha256": args.archive_sha256,
            "origin_host": b["origin_host"], "verification_host": socket.gethostname(),
            "different_hostname_observed": b["origin_host"] != socket.gethostname(),
            "independent_storage_acceptance": "REQUIRES_OPERATOR_CONFIRMATION_NOT_PROVEN_BY_HOSTNAME",
            "file_count": len(b["files"]), "snapshots": len(b["snapshots"]), "deletion_ready": False}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("backup", "verify", "diagnose"))
    for key in ("contract", "kubeconfig", "key-file", "archive", "archive-sha256", "sops", "resume-from"):
        p.add_argument("--"+key)
    p.add_argument("--output-parent", required=True)
    args = p.parse_args()
    os.umask(0o077)
    out = Path(tempfile.mkdtemp(prefix="rosa-checkpoint-", dir=args.output_parent))
    audit = {"phase":"LOCAL_INPUT_PREFLIGHT", "encryption_attempted":False, "archive_written":False}
    try:
        if args.resume_from:
            need(args.mode in ("backup","diagnose"), "RESUME_BACKUP_OR_DIAGNOSE_ONLY")
            audit["resumed_from"] = validate_resume(args.resume_from)
        if args.mode == "diagnose":
            audit["phase"] = "RECIPIENT_STRUCTURE_DIAGNOSTIC_ONLY"
            result = diagnose(args)
        else:
            need(args.key_file, "EXISTING_SEPARATE_AGE_IDENTITY_REQUIRED")
            private_file(args.key_file, 65536)
            binary = sops_binary(args, out)
            result = backup(args, out, binary, audit) if args.mode == "backup" else verify(args, out, binary)
    except Exception as error:
        result = {"status": "STOP_CHECKPOINT", "reason": str(error) if type(error) is RuntimeError else type(error).__name__, "deletion_ready": False}
    result.update(resource_mutations=0, SQL=0, sync_started=False, credential_values_printed=False)
    if args.mode in ("backup","diagnose"): result.update(audit)
    (out/"receipt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2)); print("Private checkpoint directory: "+str(out))
    return 1 if result["status"].startswith("STOP_") else 0

if __name__ == "__main__": raise SystemExit(main())
