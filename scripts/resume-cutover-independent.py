"""Resume source discovery, held resources and expired namespace ECR access."""
import argparse
import concurrent.futures
import copy
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ECR_HELPER_SHA = "0223957be6df4f25c24173b1ab3fae2c6fa4227545fffeaab420985d0bf30ee5"
PROFILES = {"backend": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "256Mi"}},
            "frontend": {"requests": {"cpu": "25m", "memory": "32Mi"}, "limits": {"cpu": "250m", "memory": "64Mi"}}}

REMOTE_READER = r'''
import hashlib,json,os,shutil,subprocess
from pathlib import Path
def run(args):
    try:
        p=subprocess.run([tool,*args],capture_output=True,timeout=25)
        return json.loads(p.stdout) if p.returncode==0 else None
    except (OSError,ValueError,subprocess.TimeoutExpired):return None
tool=shutil.which("kubectl") or shutil.which("oc")
if not tool:
    print(json.dumps({"status":"SOURCE_CLI_NOT_FOUND"}));raise SystemExit(0)
paths=set(filter(None,os.environ.get("KUBECONFIG","").split(os.pathsep)))
paths.update([str(Path.home()/".kube/config"),"/etc/kubernetes/admin.conf"])
rows=[]
for filename in sorted(paths):
    path=Path(filename).expanduser()
    if not path.is_file() or not os.access(path,os.R_OK):continue
    cfg=run(["--kubeconfig",str(path),"config","view","-o","json"])
    if cfg is None:continue
    servers={c["name"]:c["cluster"].get("server","").rstrip("/") for c in cfg.get("clusters",[])}
    for ctx in cfg.get("contexts",[]):
        server=servers.get(ctx["context"].get("cluster"),"")
        if not server.startswith("https://") or server==CLOUD_API.rstrip("/"):continue
        if SOURCE_API and server!=SOURCE_API.rstrip("/"):continue
        prefix=["--kubeconfig",str(path),"--context",ctx["name"],"--request-timeout=15s"]
        dep=run(prefix+["get","deployment","backend","-n","application","-o","json"])
        app=run(prefix+["get","application.argoproj.io","apps-backend","-n","argocd","-o","json"])
        row={"kubeconfig":str(path),"context":ctx["name"],"source_contract_match":False}
        if dep is None or app is None:
            row["status"]="TARGET_NOT_FOUND_OR_ACCESS_UNAVAILABLE";rows.append(row);continue
        source=app.get("spec",{}).get("source",{})
        match=(source.get("repoURL","").removesuffix(".git").rstrip("/")=="https://github.com/seokpan/seokpan-gitops" and source.get("path")=="apps/backend" and not app.get("spec",{}).get("sources"))
        row.update(status="READ_COMPLETED",source_contract_match=match)
        if match:
            row.update(server_sha256=hashlib.sha256(server.encode()).hexdigest(),deployment_uid=dep["metadata"]["uid"],replicas=dep["spec"].get("replicas"),sync_policy=app["spec"].get("syncPolicy",{}),active_operation=bool(app.get("operation")),parent_references=[{"kind":p["kind"],"name":p["name"]} for p in app["metadata"].get("ownerReferences",[])])
            writers=run(prefix+["get","hpa,jobs,cronjobs","-n","application","-o","json"])
            row["writer_inventory"]=None if writers is None else [{"kind":x["kind"],"name":x["metadata"]["name"],"suspend":x.get("spec",{}).get("suspend"),"active":x.get("status",{}).get("active"),"scale_target":x.get("spec",{}).get("scaleTargetRef")} for x in writers.get("items",[])]
        rows.append(row)
print(json.dumps({"status":"SOURCE_SSH_READ_COMPLETED","contexts":rows,"writer_stop_ready":False}))
'''


class Failure(Exception):
    pass


def require(value, code):
    if not value:
        raise Failure(code)


def read(path):
    return json.loads(Path(path).read_text())


def write(folder, name, value):
    (folder / name).write_text(json.dumps(value, indent=2), encoding="utf-8")


class Cloud:
    def __init__(self, oc, kubeconfig, folder):
        self.oc, self.folder = str(oc), folder
        self.env = dict(os.environ, KUBECONFIG=str(kubeconfig), AWS_PAGER="", AWS_MAX_ATTEMPTS="1")

    def run(self, args, label, payload=None, ok=(0,), timeout=45):
        result = subprocess.run([self.oc, *args], env=self.env, input=payload, capture_output=True, timeout=timeout)
        if result.returncode not in ok:
            text = result.stderr.decode(errors="replace").lower()
            category = "ACCESS_DENIED" if "forbidden" in text or "unauthorized" in text else "RESOURCE_POLICY" if "quota" in text or "limitrange" in text else "NETWORK_OR_TLS" if any(x in text for x in ("timeout", "certificate", "connection refused")) else "CLI_OR_API_ERROR"
            raise Failure(re.sub("[^A-Z0-9_]", "_", label.upper()) + "_" + category)
        return result

    def get(self, args, label):
        result = self.run([*args, "-o", "json", "--request-timeout=20s"], label)
        return json.loads(result.stdout) if result.stdout.strip() else None


def source_inventory(home, target, api, folder, source_api=""):
    aliases, files = set(), set()
    ssh = home / ".ssh/config"
    if ssh.is_file() and ssh.stat().st_size <= 262144:
        for line in ssh.read_text(errors="replace").splitlines():
            tokens = line.strip().split()
            if tokens and tokens[0].lower() == "host":
                aliases.update(t for t in tokens[1:] if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", t))
    for pattern in ("work/*/ansible/inventory*", "work/*/ansible/inventories/*/hosts*", "work/*/inventory*", "ansible/inventory*", "ansible/hosts*"):
        for path in home.glob(pattern):
            candidates = [path] if path.is_file() else list(path.glob("*")) if path.is_dir() else []
            files.update(p for p in candidates if p.is_file() and p.suffix.lower() in ("", ".ini", ".yml", ".yaml") and p.stat().st_size <= 262144)
    inventory = []
    for path in sorted(files)[:40]:
        names = set()
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";", "[")) or re.search(r"password|token|secret|private.key", line, re.I):
                continue
            token = line.split()[0].rstrip(":")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", token) and token not in ("all", "hosts", "children", "vars"):
                names.add(token)
        inventory.append({"file": str(path), "candidate_names": sorted(names)})
    result = {"status": "SOURCE_CANDIDATES_COLLECTED", "ssh_aliases": sorted(aliases), "ansible_inventory_candidates": inventory,
              "automatic_ssh_to_candidates": False, "writer_stop_ready": False}
    if target:
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", target), "SOURCE_SSH_TARGET_FORMAT")
        require(source_api.startswith("https://"), "EXPECTED_SOURCE_API_REQUIRED")
        code = "CLOUD_API=" + repr(api) + "\nSOURCE_API=" + repr(source_api) + "\n" + REMOTE_READER
        try:
            proc = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", target, "python3 -"], input=code.encode(), capture_output=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            result["remote"] = {"status": "SOURCE_SSH_UNAVAILABLE_OR_TIMEOUT"}
        else:
            if proc.returncode == 0:
                try:
                    result["remote"] = json.loads(proc.stdout)
                except ValueError:
                    result["remote"] = {"status": "SOURCE_SSH_JSON_NOT_RETURNED"}
            else:
                text = proc.stderr.decode(errors="replace").lower()
                reason = "HOST_KEY_CONFIRMATION_REQUIRED" if "host key verification failed" in text else "SSH_AUTH_REQUIRED" if "permission denied" in text else "SOURCE_SSH_CONNECTION_FAILED"
                result["remote"] = {"status": reason}
    else:
        result["remote"] = {"status": "SOURCE_ACCOUNT_HOST_REQUIRED_NO_GUESS"}
    write(folder, "source-result.json", result)
    return result


def resources_stage(cloud, held, pool_folder, folder):
    bundle = copy.deepcopy(read(held / "held-render.json"))
    patch = read(pool_folder / "backend-pool.DO_NOT_APPLY.candidate.json")
    updated = []
    for obj in bundle["items"]:
        if obj.get("kind") != "Deployment":
            continue
        name = obj["metadata"]["name"]
        require(name in PROFILES and obj["metadata"].get("namespace") == "seokpan-cloud" and obj["spec"].get("replicas") == 0, "HELD_DEPLOYMENT_REQUIRED")
        containers = obj["spec"]["template"]["spec"]["containers"]
        require(len(containers) == 1 and containers[0]["name"] == name, "EXACT_APPLICATION_CONTAINER_REQUIRED")
        container = containers[0]
        container["resources"] = copy.deepcopy(PROFILES[name])
        if name == "backend":
            obj["spec"]["strategy"] = copy.deepcopy(patch["spec"]["strategy"])
            candidate = patch["spec"]["template"]["spec"]["containers"][0]
            container.update(command=candidate["command"], args=candidate["args"])
            merged = {x["name"]: x for x in container.get("env", [])}
            merged.update({x["name"]: x for x in candidate["env"]})
            container["env"] = list(merged.values())
        updated.append(obj)
    require(len(updated) == 2, "TWO_DEPLOYMENTS_REQUIRED")
    write(folder, "held-with-resources-and-pool.DO_NOT_APPLY.json", bundle)
    limits = cloud.get(["get", "resourcequota,limitrange", "-n", "seokpan-cloud"], "namespace-resource-policy")
    write(folder, "namespace-resource-policy.json", limits)
    cloud.run(["apply", "--server-side", "--dry-run=server", "--field-manager=seokpan-held-preparation", "-f", "-", "--request-timeout=30s"], "changed-deployments-admission", json.dumps({"apiVersion": "v1", "kind": "List", "items": updated}).encode(), timeout=75)
    result = {"status": "PASS_CHANGED_DEPLOYMENTS_RESOURCE_POOL_ADMISSION", "changed_deployments": 2, "replicas": 0,
              "profiles": PROFILES, "profile_basis": "EXISTING_LAB_INITIAL_VALIDATION_VALUES_NOT_CLOUD_LOAD_ACCEPTANCE",
              "load_and_scheduling_acceptance": False, "runtime_pool_adopted": False, "objects_persisted": 0}
    write(folder, "resource-result.json", result)
    return result


def pull_stage(cloud, helper_path, namespace, account, user, backend, renew, folder):
    secret = cloud.get(["get", "secret", "seokpan-cloud-ecr-pull", "-n", namespace], "existing-project-pull-secret")
    require(secret is not None, "EXISTING_PROJECT_PULL_SECRET_REQUIRED")
    require(secret.get("type") == "kubernetes.io/dockerconfigjson", "PROJECT_PULL_SECRET_TYPE")
    require(all(secret["metadata"].get("labels", {}).get(k) == v for k, v in {"app.kubernetes.io/part-of": "seokpan", "seokpan.io/environment": "cloud"}.items()), "PROJECT_PULL_SECRET_OWNERSHIP")
    expiry = secret["metadata"].get("annotations", {}).get("seokpan.io/pull-expires-at")
    require(isinstance(expiry, str), "PROJECT_PULL_EXPIRY_REQUIRED")
    expires = datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    needs = expires <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    secret.clear()
    if needs:
        require(renew, "EXPIRED_PROJECT_PULL_RENEWAL_NOT_ENABLED")
        require(cloud.get(["get", "serviceaccount", "seokpan-cloud-runtime", "-n", namespace], "existing-runtime-serviceaccount") is not None, "EXISTING_RUNTIME_SERVICEACCOUNT_REQUIRED")
        require(hashlib.sha256(helper_path.read_bytes()).hexdigest() == ECR_HELPER_SHA, "ECR_HELPER_HASH_MISMATCH")
        spec = importlib.util.spec_from_file_location("pinned_ecr_helper", helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        result = helper.configure_ecr_pull(cloud, namespace, account, user, backend)
        result = dict(result, status="PASS_EXISTING_PROJECT_PULL_RENEWED", secret_changed=True)
    else:
        result = {"status": "PASS_CURRENT_PROJECT_PULL_REUSED", "expires_at_utc": expiry, "secret_changed": False}
    result.update(native_worker_role_pull="NOT_TESTED_POLICY_APPLY_RESULT_PENDING", app_pods_created=0, sql_calls=0)
    write(folder, "pull-result.json", result)
    return result


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    for key in ("resume-from", "pool-from", "held-from", "cloud-kubeconfig", "ecr-helper"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--cloud-api", required=True)
    p.add_argument("--expected-aws-user", required=True)
    p.add_argument("--source-ssh", default="")
    p.add_argument("--source-api", default="")
    p.add_argument("--renew-expired-pull", action="store_true")
    args = p.parse_args()
    root = args.root.resolve()
    for path in (args.resume_from, args.pool_from, args.held_from):
        require(path.resolve().parent == root, "RESULT_DIRECTORY_SCOPE")
    previous = read(args.resume_from / "receipt.json")
    require(previous["status"] == "READ_ONLY_CUTOVER_PREPARATION_COLLECTED", "COMPLETED_READINESS_REQUIRED")
    folder = Path(tempfile.mkdtemp(prefix="cloud-independent-resume-", dir=root))
    oc = shutil.which("oc") or str(Path.home() / ".local/share/seokpan/oc-cli-4.20/oc")
    cloud = Cloud(oc, args.cloud_kubeconfig, folder)
    server = cloud.run(["whoami", "--show-server", "--request-timeout=15s"], "cloud-target-guard").stdout.decode().strip()
    require(server.rstrip("/") == args.cloud_api.rstrip("/"), "CLOUD_TARGET_CHANGED")
    inputs = read(root / "protected/inputs.tfvars.json")
    held = read(args.held_from / "held-render.json")
    backend = [x["spec"]["template"]["spec"]["containers"][0]["image"] for x in held["items"] if x.get("kind") == "Deployment" and x["metadata"]["name"] == "backend"]
    require(len(backend) == 1, "ACCEPTED_BACKEND_IMAGE_REQUIRED")
    jobs = {
        "source": lambda: source_inventory(Path.home(), args.source_ssh, args.cloud_api, folder, args.source_api),
        "resources": lambda: resources_stage(cloud, args.held_from, args.pool_from, folder),
        "ecr": lambda: pull_stage(cloud, args.ecr_helper, "seokpan-cloud", inputs["foundation"]["account_id"], args.expected_aws_user, backend[0], args.renew_expired_pull, folder)}
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(job): key for key, job in jobs.items()}
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as error:
                results[key] = {"status": "STOP_INDEPENDENT_STAGE", "reason": str(error) if isinstance(error, Failure) else type(error).__name__, "automatic_retry": False}
            print(json.dumps({"stage": key, "result": results[key]}), flush=True)
    summary = {"status": "THREE_INDEPENDENT_STAGES_FINISHED", "steps": results, "completed_tests_repeated": False,
               "writer_stop_ready": False, "writer_stop": False, "sql_calls": 0, "database_writes": 0,
               "application_started": False, "gitops_sync": False, "terraform_or_iam_changes": False}
    write(folder, "receipt.json", summary)
    print(json.dumps(summary))
    print("Private result directory: " + str(folder))


if __name__ == "__main__":
    main()
