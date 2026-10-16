# ROSA checkpoint, scoped lifecycle and held restoration

The tools separate configuration preservation, independent access, Terraform plan preparation, human-approved execution, and functional restoration. A successful config archive is not a database/PVC backup or deletion approval. CI uses synthetic inputs and does not access AWS, OCM, a cluster, or production credentials.

## Commands

`python3 scripts/prepare-rosa-checkpoint.py backup --contract /protected/contract.json --kubeconfig /protected/personal-kubeconfig --key-file /separate/age-identity.txt --output-parent /protected/checkpoints`

The contract supplies schema1, expected_api, previous_inventory, inventory_counts (pvc/cloud_secrets), cloud_namespace, argo_namespace, argo_name, projects, applications, identity_providers, groups, local_roots, recipient_source and recipient_source_sha256. These are protected deployment inputs; there are no live IDs or credential values in the repository. The previous inventory supplies the accepted PVC/Cloud Secret metadata and file-candidate list. Those inventory queries and the denied Router exec/GitOps Secret list are not retried. Required Cloud Secret values are captured for the first encrypted archive. ServiceAccount tokens and age decryption identities are excluded. Plaintext exists in process memory; no plaintext snapshot is written. The script encrypts to the existing age recipient, verifies local decryption/content equality and prints only a sanitized receipt. If SOPS is absent on Linux amd64, a private copy of3.13.3 is downloaded and compared with its pinned binary SHA256. Existing separate age identity remains unchanged.

Recipient preflight uses the hash-pinned encrypted source before cluster reads. It accepts the public recipient inside SOPS JSON metadata or standard block-YAML metadata, including quoted keys and indentless age lists; it does not infer input format from the filename or payload fields. Missing/multiple recipients and duplicate JSON keys stop without printing values. The receipt records the format, phase and encryption/archive attempts. For a prior ONE_EXISTING_AGE_RECIPIENT_REQUIRED stop with no archive, use backup --resume-from /protected/previous-checkpoint. Other failures or an existing encrypted archive require reconciliation rather than blind restart. In-memory configuration from the failed attempt was not an accepted saved backup and must be captured for the first complete archive; completed runtime tests are not repeated.

If the recipient stop remains unresolved, do not retry backup or assume it is a JSON problem. Use `diagnose --contract /protected/contract.json --resume-from /protected/previous-checkpoint --output-parent /protected/diagnostics`. This mode checks the pinned encrypted-source hash and reports metadata counts/scalar styles only. It does not read an age identity, run SOPS/decrypt, access the cluster, choose recipients or create an archive. YAML diagnostics are textual structure, not semantic YAML/crypto acceptance. Zero, multiple and unsupported scalar cases remain distinct without exposing values. Do not reduce a multi-recipient source to its first recipient or encrypt expanded cluster data to every existing recipient without an accepted key/access contract. PVC disposition and local Plan-input discovery are independent read-only stages and must not be skipped because this diagnostic is pending.

`python3 scripts/prepare-rosa-checkpoint.py verify --archive /independent/checkpoint.sops.json --archive-sha256 <received-sha256> --key-file /separate/age-identity.txt --sops /trusted/sops --output-parent /independent/verification`

Copy only the encrypted archive, sanitized receipt and pinned verification tool to storage outside the Controller/lab failure domain. Transfer the age identity through its separately accepted path, never inside the archive. On Windows use an independently verified SOPS executable and a protected identity file. Verify archive SHA256, real decryption and every stored file hash. The verifier does not extract old files or apply manifests. Different hostnames alone do not prove independent storage; storage owner/access acceptance is additional evidence. The tool intentionally never outputs deletion_ready=true.

`python3 scripts/rosa-lifecycle-plan.py plan --action delete --tf-root /reviewed/source/terraform/rosa --tf-data /existing/tf-data --var-file /protected/current.tfvars.json --cluster-id <current-cluster-id> --output-parent /protected/plans`

Supply the existing purpose AWS session and RHCS_TOKEN through their accepted helpers, not through Git or command-line values. The plan helper requires the initialized S3 rosa Backend key, expected account and seokpan-tf-rosa role, reads current remote State, saves its lineage/serial and prepares a full destroy Plan. Only the exact rosa resource type/name pairs are allowed; Foundation/bootstrap, module changes, mixed changes, replacements or another cluster are rejected. Source/Lock/input/plan hashes are bound in a private manifest. Plan logs/JSON/State may contain protected values: encrypt and independently verify these artifacts as a checkpoint supplement before approving deletion. State locking during Plan is not a resource deletion. No init, state import/removal, target shortcut or Foundation destroy is performed.

For recreation use `--action create --cluster-id NEW:<cluster_name>` with fresh reviewed inputs and `worker_sg_binding=null`. Existing cluster State blocks recreation. Worker SG binding is a second stage after fresh Worker/Machine ownership is observed; an old SG must not be copied from the archive into new-cluster inputs. Old saved Apply plans are history and must not be replayed.

`python3 scripts/rosa-lifecycle-plan.py apply --manifest /protected/plan-manifest.json --approval-file /protected/explicit-approval.json --output-parent /protected/execution`

Execution requires a new, explicit human approval of the specific action/cluster/plan hash. A previously requested preparation, backup PASS or code review is not that approval. The approval file contains action, cluster_id, plan_sha256, approved_by_user=true, approval_reference, and the preservation/owner/data-release/recreation gates shown below. These assertions are operator evidence, not a cryptographic proof of who approved. Never fabricate them. The interactive exact phrase, unchanged source/input/plan, current role and unchanged State lineage/serial are checked again. An exclusive attempt marker is created before Apply. Any partial or unknown result requires read-only reconciliation; do not rerun or remove the marker to retry.

Approval template (unaccepted; never auto-fill the booleans):

```json
{
  "action": "delete",
  "cluster_id": "INPUT_REQUIRED",
  "plan_sha256": "INPUT_REQUIRED",
  "approved_by_user": false,
  "approval_reference": "",
  "encrypted_backup_accepted": false,
  "independent_decrypt_access_accepted": false,
  "pvc_data_disposition_accepted": false,
  "state_owner_accepted": false,
  "data_operations_released": false,
  "recreation_inputs_accepted": false
}
```

## Required preservation decisions

- The GitOps Secret-list denial stays recorded. Operator-generated server signing/TLS/session/cache inputs may need regeneration. Private repo credentials, custom TLS and other manually supplied credentials need an owner export or an explicit, tested re-supply source. Do not classify every inaccessible Secret as disposable. Delete approval remains blocked until that gap is classified.
- PVC metadata4 is an inventory, not data. For each claim identify owner, data purpose, PV/CSI/EBS, reclaim policy, backup/snapshot and restoration evidence, or justified cache/regeneration disposition. A plaintext YAML export cannot preserve volume bytes.
- RDS/Valkey/backups/Registry/common IAM/VPC and evidence storage are outside rosa deletion scope. Temporary data-admin use must end and required outputs must be retained. Source writers and the separate migration hold are not automatically changed by rosa cleanup. NAT/EIP cleanup needs a separate dependency/cost review.
- Capture current ROSA State and relevant Foundation/bootstrap State owner/version evidence without replacing or hand-editing backend State. Preserve exact source, provider lock, actual inputs, Runtime/controller permissions and image/config/release revisions.

## Same functional environment restoration contract

Recreation is not complete after Terraform alone. Restore the following layers in order, with new identity mappings, and keep unresolved gates false:

| Layer | Must preserve/reconstruct | New-environment acceptance |
| --- | --- | --- |
| Platform | Exact adopted OpenShift version/Classic Multi-AZ/topology, operator install declarations, node/resources | New Cluster/API/Ingress and infrastructure ownership; supported version/cost plan |
| Data connectivity | Foundation endpoints/CA, Worker ECR role, Worker-to-Data rules managed in rosa State | Fresh Worker/Machine/SG mapping and intended3306/6379 connectivity |
| Human management | GitHub IDP, selected users/groups, dedicated-admin grant, emergency path, Argo SSO/RBAC | New OAuth callback, new sessions, intended allowed/denied scopes |
| Runtime inputs | Original protected inputs/CA/logical Secret names, runtime SA, resource and held Pool proposals | Correct new Namespace UID/secret consumers; no expired token replay |
| GitOps | Exact AppProject/Application source revision/patches and controller namespace Role/NP/custom-host rule | Rebuild operator-managed fields; source/render correspondence; FE/BE0 and no automatic Sync |
| Monitoring | Adopted monitoring revision,18 permissions and manual policy | Restore declarations first; targets/rules/notification acceptance follows app activation |
| App execution | Approved FE/BE digests, configuration, rollout/Pool and Schema contract | Final Data/current/operating Pool acceptance before explicit app activation |
| Evidence | Existing completed source/UI/TLS/Pull/monitoring/Recovery results and current limits | Keep old results; test only the new environment's changed identities/connections/runtime dependencies |

Never replay live metadata UID/resourceVersion/status, old API/Ingress/OIDC/Worker SG references, expiring service tokens or old server certificates unchanged. Fresh-cluster bootstrap/identity mapping and held restoration must be reviewed before apply; these helpers do not yet perform automatic operator installation or whole-manifest restoration. Keep the integration PR Draft until that restoration orchestrator, input-gap decisions, independent access and actual safe plans are accepted. Do not claim that deployment has been restored merely because lifecycle code exists or Apply exits0.

The restoration acceptance must compare the implementation, connectivity and execution state separately. Record an adopted revision for each operator/bootstrap/permission/runtime/monitoring recipe and an object-level restored/mapped/pending inventory. Include controller/server/ApplicationSet permissions, subscriptions and installed operator versions, cluster-scoped bindings, OAuth integration inputs, external callback/DNS mapping, storage disposition and re-supply sources. The current checkpoint capture alone is not proof that every cluster-scoped setting has been captured. An absent recipe, inaccessible manual input, or unexplained desired-state difference blocks the claim of equivalent restoration. Preserve the actual held FE/BE0, manual Sync policy and migration hold; reconstruction must not activate an app that was not running before deletion.

## Offline work

Preserve the code and encrypted evidence outside inaccessible lab machines first. Source/PR review, restore mapping/recipes, Release/rollback records, evidence index and report/slide work can continue offline. Actual cluster/DB/runtime PASS requires its environment and is not fabricated. Final deletion or whole clean-recreate/E2E completion remains separate from this tooling.
