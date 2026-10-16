"""ROSA-only saved-plan preparation and explicitly approved execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

OWNED = {("rhcs_cluster_rosa_classic", "cluster"), ("rhcs_rosa_oidc_config", "cluster"),
         ("aws_iam_openid_connect_provider", "cluster"), ("aws_iam_role", "operator"),
         ("aws_iam_role_policy_attachment", "operator"),
         ("aws_vpc_security_group_ingress_rule", "worker_to_data"), ("terraform_data", "input_contract")}

def need(ok, code):
    if not ok: raise RuntimeError(code)

def read_json(path):
    p = Path(path)
    need(p.is_file() and not p.is_symlink(), "REGULAR_LOCAL_INPUT_REQUIRED")
    if os.name == "posix":
        s=p.stat()
        need(s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) & 0o077 == 0, "PRIVATE_OWNED_INPUT_REQUIRED")
    return json.loads(p.read_text(encoding="utf-8"))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def scope(plan, action, cluster_id):
    counts = {}
    clusters = []
    for x in plan.get("resource_changes", []):
        if x.get("mode") == "data": continue
        acts = x["change"]["actions"]
        if acts == ["no-op"]: continue
        need(not x.get("module_address") and (x["type"], x["name"]) in OWNED, "FOREIGN_ROOT_OR_RESOURCE_IN_PLAN")
        need(acts == ["delete" if action == "delete" else "create"], "REPLACEMENT_OR_UNEXPECTED_ACTION_REJECTED")
        counts[x["type"]] = counts.get(x["type"], 0) + 1
        if x["type"] == "rhcs_cluster_rosa_classic":
            clusters.append(x)
            if action == "delete": need(x["change"]["before"].get("id") == cluster_id, "DELETE_CLUSTER_ID_MISMATCH")
    need(len(clusters) == 1, "EXACTLY_ONE_CLUSTER_CHANGE_REQUIRED")
    need(not plan.get("errored", False) and plan.get("applyable", True), "NON_APPLYABLE_PLAN")
    return counts

def source_hash(root):
    sources = [*root.glob("*.tf"), *root.glob("*.tf.json")]
    files = sorted([*sources, root/".terraform.lock.hcl"])
    need(sources and (root/".terraform.lock.hcl").is_file(), "SOURCE_AND_LOCK_REQUIRED")
    need(all(p.is_file() and not p.is_symlink() for p in files), "REGULAR_SOURCE_AND_LOCK_REQUIRED")
    need(not list(root.glob("*.auto.tfvars*")) and not (root/"terraform.tfvars").exists() and not (root/"terraform.tfvars.json").exists(), "UNREVIEWED_AUTO_INPUTS_REJECTED")
    return hashlib.sha256("\n".join(p.name+":"+sha(p) for p in files).encode()).hexdigest()

def environment(root, data):
    need(root.name == "rosa" and root.parent.name == "terraform", "ROSA_ROOT_ONLY")
    meta = read_json(data/"terraform.tfstate").get("backend", {})
    need(meta.get("type") == "s3" and meta.get("config", {}).get("key") == "phase2/rosa/terraform.tfstate", "ROSA_BACKEND_KEY_REQUIRED")
    need(meta.get("config", {}).get("region") == "ap-northeast-2", "BACKEND_REGION_MISMATCH")
    env = {k:v for k,v in os.environ.items() if not k.startswith(("TF_CLI_ARGS", "TF_VAR_", "TF_LOG"))}
    env.update(TF_DATA_DIR=str(data), TF_WORKSPACE="default", TF_IN_AUTOMATION="1", CHECKPOINT_DISABLE="1", AWS_REGION="ap-northeast-2", AWS_DEFAULT_REGION="ap-northeast-2", AWS_PAGER="")
    return env

def call(cmd, env, out):
    p = subprocess.run(cmd, capture_output=True, env=env, timeout=7200)
    if p.returncode not in (0, 2):
        (out/"failure-private.log").write_bytes(p.stdout+p.stderr)
        raise RuntimeError("COMMAND_FAILED_PRIVATE_LOG_ONLY_NO_RETRY")
    return p

def prepare(a, out):
    root, data, vars_file = Path(a.tf_root).resolve(), Path(a.tf_data).resolve(), Path(a.var_file).resolve()
    env = environment(root, data)
    initial_source_hash = source_hash(root)
    values = read_json(vars_file)
    account = str(values.get("foundation", {}).get("account_id", ""))
    need(re.fullmatch(r"[0-9]{12}", account) is not None, "EXPECTED_ACCOUNT_INPUT_REQUIRED")
    caller = json.loads(call(["aws", "sts", "get-caller-identity", "--output", "json"], env, out).stdout)
    need(caller.get("Account") == account and ":assumed-role/seokpan-tf-rosa/" in caller.get("Arn", ""), "PURPOSE_ROLE_REQUIRED")
    need(env.get("RHCS_TOKEN"), "EXISTING_RHCS_SESSION_REQUIRED")
    need(values.get("cluster_enabled") is True, "EXPLICIT_CLUSTER_ENABLED_INPUT_REQUIRED")
    if a.action == "create": need(values.get("worker_sg_binding") is None, "NEW_CLUSTER_REQUIRES_UNBOUND_WORKER_STAGE")
    tf = ["terraform", "-chdir="+str(root)]
    version = json.loads(call(tf+["version", "-json"], env, out).stdout)
    need(version.get("terraform_version") == "1.16.4", "ADOPTED_TERRAFORM_VERSION_REQUIRED")
    state = call(tf+["state", "pull"], env, out).stdout
    state_obj = json.loads(state)
    (out/"before.tfstate").write_bytes(state)
    if a.action == "delete":
        ids = [i.get("attributes", {}).get("id") for r in state_obj.get("resources", [])
               if r.get("mode") == "managed" and r.get("type") == "rhcs_cluster_rosa_classic" for i in r.get("instances", [])]
        need(ids == [a.cluster_id], "CURRENT_STATE_CLUSTER_ID_MISMATCH")
    else:
        need(not any(r.get("mode") == "managed" and r.get("type") == "rhcs_cluster_rosa_classic" and r.get("instances") for r in state_obj.get("resources", [])), "EXISTING_CLUSTER_STATE_BLOCKS_RECREATE")
        need(a.cluster_id == "NEW:"+values.get("cluster_name", ""), "NEW_CLUSTER_NAME_CONFIRMATION_TARGET_REQUIRED")
    plan = out/"saved.tfplan"
    args = ["plan", "-input=false", "-no-color", "-lock-timeout=60s", "-detailed-exitcode", "-var-file="+str(vars_file), "-out="+str(plan)]
    if a.action == "delete": args.insert(1, "-destroy")
    p = call(tf+args, env, out)
    (out/"plan-private.log").write_bytes(p.stdout+p.stderr)
    rendered = call(tf+["show", "-json", str(plan)], env, out).stdout
    (out/"plan-private.json").write_bytes(rendered)
    counts = scope(json.loads(rendered), a.action, a.cluster_id)
    need(source_hash(root) == initial_source_hash, "SOURCE_CHANGED_DURING_PLAN")
    manifest = {"schema":1, "action":a.action, "cluster_id":a.cluster_id, "cluster_name":values.get("cluster_name"),
                "tf_root":str(root), "tf_data":str(data), "var_file":str(vars_file), "var_sha256":sha(vars_file),
                "source_hash":initial_source_hash, "plan":str(plan), "plan_sha256":sha(plan),
                "state_lineage":state_obj.get("lineage"), "state_serial":state_obj.get("serial"),
                "account_id":account, "counts":counts, "user_approval_received":False, "execution_started":False}
    (out/"plan-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"status":"PASS_ROSA_ONLY_SAVED_PLAN_PREPARED_NOT_APPLIED", "action":a.action, "plan_sha256":manifest["plan_sha256"], "counts":counts,
            "approval_required":True, "apply_started":False, "protected_files_require_encrypted_independent_copy":True}

def apply(a, out):
    need(a.approval_file and a.manifest, "EXPLICIT_APPROVAL_FILE_AND_MANIFEST_REQUIRED")
    m, approved = read_json(a.manifest), read_json(a.approval_file)
    need(m.get("action") in ("create", "delete"), "KNOWN_PLAN_ACTION_REQUIRED")
    marker = Path(a.manifest).parent/"apply-started.NO_AUTOMATIC_RETRY.json"
    need(not marker.exists(), "PREVIOUS_APPLY_REQUIRES_READ_ONLY_RECONCILIATION")
    need(approved.get("action") == m["action"] and approved.get("cluster_id") == m["cluster_id"]
         and approved.get("plan_sha256") == m["plan_sha256"] and approved.get("approved_by_user") is True
         and isinstance(approved.get("approval_reference"), str) and len(approved["approval_reference"]) >= 8, "EXPLICIT_PLAN_BOUND_USER_APPROVAL_REQUIRED")
    required = ("encrypted_backup_accepted", "independent_decrypt_access_accepted", "pvc_data_disposition_accepted", "state_owner_accepted", "data_operations_released", "recreation_inputs_accepted")
    need(all(approved.get(k) is True for k in required), "PRESERVATION_AND_OWNER_GATES_REQUIRED")
    need(sha(m["plan"]) == m["plan_sha256"] and sha(m["var_file"]) == m["var_sha256"] and source_hash(Path(m["tf_root"])) == m["source_hash"], "PLAN_INPUT_SOURCE_CHANGED")
    need(sys.stdin.isatty(), "INTERACTIVE_HUMAN_CONFIRMATION_REQUIRED")
    phrase = m["action"].upper()+" "+str(m["cluster_id"])+" "+m["plan_sha256"][:12]
    need(input("Type exactly "+phrase+": ") == phrase, "HUMAN_CONFIRMATION_MISMATCH")
    env = environment(Path(m["tf_root"]), Path(m["tf_data"]))
    caller = json.loads(call(["aws", "sts", "get-caller-identity", "--output", "json"], env, out).stdout)
    need(caller.get("Account") == m["account_id"] and ":assumed-role/seokpan-tf-rosa/" in caller.get("Arn", ""), "PURPOSE_ROLE_REQUIRED")
    need(env.get("RHCS_TOKEN"), "EXISTING_RHCS_SESSION_REQUIRED")
    tf = ["terraform", "-chdir="+m["tf_root"]]
    scope(json.loads(call(tf+["show", "-json", m["plan"]], env, out).stdout), m["action"], m["cluster_id"])
    current = json.loads(call(tf+["state", "pull"], env, out).stdout)
    need((current.get("lineage"), current.get("serial")) == (m["state_lineage"], m["state_serial"]), "STATE_CHANGED_REPLAN_REQUIRED")
    with marker.open("x", encoding="utf-8") as f: json.dump({"plan_sha256":m["plan_sha256"], "action":m["action"]}, f)
    result = call(tf+["apply", "-input=false", "-no-color", "-lock-timeout=60s", m["plan"]], env, out)
    need(result.returncode == 0, "APPLY_NONZERO_PARTIAL_OUTCOME_NO_RETRY")
    (out/"apply-private.log").write_bytes(result.stdout+result.stderr)
    return {"status":"SAVED_PLAN_APPLY_EXIT_0_RECONCILIATION_REQUIRED", "action":m["action"], "asynchronous_cleanup_or_runtime_acceptance":"NOT_VERIFIED"}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=("plan","apply"))
    p.add_argument("--action",choices=("create","delete"))
    for x in ("tf-root","tf-data","var-file","cluster-id","manifest","approval-file"):
        p.add_argument("--"+x)
    p.add_argument("--output-parent",required=True)
    a=p.parse_args();os.umask(0o077)
    out=Path(tempfile.mkdtemp(prefix="rosa-lifecycle-",dir=a.output_parent))
    try:
        if a.mode=="plan": need(all((a.action,a.tf_root,a.tf_data,a.var_file,a.cluster_id)),"EXPLICIT_PLAN_INPUTS_REQUIRED")
        result=prepare(a,out) if a.mode=="plan" else apply(a,out)
    except Exception as e:
        result={"status":"STOP_ROSA_LIFECYCLE","reason":str(e) if type(e) is RuntimeError else type(e).__name__,"automatic_retry":False}
    (out/"receipt.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2));print("Private lifecycle directory: "+str(out))
    return 1 if result["status"].startswith("STOP_") else 0

if __name__=="__main__":raise SystemExit(main())
