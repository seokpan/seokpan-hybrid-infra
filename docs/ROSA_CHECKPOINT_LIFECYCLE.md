# ROSA checkpoint, scoped lifecycle and held restoration

The tools separate configuration preservation, independent access, Terraform plan preparation, human-approved execution, and functional restoration. A successful config archive is not a database/PVC backup or deletion approval. CI uses synthetic inputs and does not access AWS, OCM, a cluster, or production credentials.

## Commands

The repository entry points are `bash scripts/rosa-delete.sh` and `bash scripts/rosa-create.sh`. Both accept `--config /private/rosa-lifecycle.json`, or use ROSA_LIFECYCLE_CONFIG / $HOME/.config/seokpan/rosa-lifecycle.json. They authenticate with the existing OCM session (device login if required), request one AWS MFA code and use only the reviewed ROSA-role helper. No credentials are embedded in the clone.

Deletion consumes the existing saved Plan and a separate Plan-bound user approval. It never marks preservation gates true or regenerates a Plan implicitly. The final exact terminal phrase and the State/Source/Plan checks remain mandatory. Apply exit0 is followed by a ROSA Root State check; live ROSA-owned AWS resources still require reconciliation. The entry points contain no NAT/EIP or Foundation cleanup command. Network deletion/recreation belongs to its separate owner; only network-ready inputs are consumed before ROSA creation. No database migration or source-writer stop is part of these commands.

Creation starts from accepted unbound Worker inputs, a reviewed clone and current network/budget acceptance. It prepares a fresh full-root Plan and requires the exact interactive create phrase. Before making paid resources it requires a hash-pinned held-restoration driver; a missing/unreviewed driver stops early. The driver receives --platform-handoff, --lifecycle-config and --receipt, and must produce PASS_ROSA_HELD_RESTORATION bound to the new cluster, Backend/Frontend0, automatic_sync=false and verified fresh Worker Data binding. A failed restoration is retained for reconciliation, never retried by recreating another cluster. This entry integration does not claim that a site-specific restoration driver already exists or is accepted.

Private config schema1 contains scope=ROSA_ONLY, auth.rosa_cli, auth.tf_session_helper, evidence_root, tf_data, current_cluster_id; delete.manifest/approval_file; and create.var_file/cluster_name/inputs_accepted/network_ready/budget_accepted/restore_driver/restore_driver_sha256/held_restoration_contract_accepted. Delete approval has the existing six preservation gates; create approval instead has recreation_inputs_accepted/network_ready/budget_accepted. Separate-owner network work is not requested as a second approval for ROSA deletion.

If the first batch completed a config archive but stopped at EXISTING_ROSA_TOKEN_REFRESH before any lifecycle receipt, append --resume-plan-from /protected/previous-batch to the same nine arguments. The old handoff, accepted config receipt, encrypted archive hash and separate-key hash must match; an existing lifecycle attempt blocks this path. This reuses the archive without repeating cluster capture or local decryption. It explicitly renews the failed OCM session through device login, then obtains a generated access token for RHCS. The ROSA --refresh flag selects the refresh token; it is not a command to renew an expired login. A pre-token failure does not show the AWS MFA prompt. A verified config archive can be independently copied/decrypted regardless of Plan readiness. NOT_RUN_NO_PLAN_RECEIPT identifies a missing supplement rather than reporting an unexecuted supplement as successful.

The optional `scripts/rosa-preservation-batch.sh` accepts ROOT, CONTRACT, PREVIOUS_STOP, KUBECONFIG, KEY, TF_DATA, VAR_FILE, CLUSTER_ID and ROSA_CLI. Keep the checkpoint and lifecycle Python files beside it. It first completes configuration encryption/local decrypt, then refreshes an existing ROSA login and asks for a hidden MFA code for the existing scoped AWS helper. It selects the current bound input, prepares a ROSA-only destroy Plan without Apply, encrypts the Plan/State supplement, and emits a private handoff manifest for verified archives and the separate existing identity. Failed Plan/auth does not discard a valid config archive; stage/exit status stays explicit. It never creates approval booleans or performs deletion, snapshots, SQL, Sync or app activation.

`python3 scripts/prepare-rosa-checkpoint.py backup --contract /protected/contract.json --kubeconfig /protected/personal-kubeconfig --key-file /separate/age-identity.txt --output-parent /protected/checkpoints`

The contract supplies schema1, expected_api, previous_inventory, inventory_counts (pvc/cloud_secrets), cloud_namespace, argo_namespace, argo_name, projects, applications, identity_providers, groups, local_roots, recipient_source and recipient_source_sha256. These are protected deployment inputs; there are no live IDs or credential values in the repository. The previous inventory supplies the accepted PVC/Cloud Secret metadata and file-candidate list. Those inventory queries and the denied Router exec/GitOps Secret list are not retried. Required Cloud Secret values are captured for the first encrypted archive. ServiceAccount tokens and age decryption identities are excluded. Plaintext exists in process memory; no plaintext snapshot is written. The script encrypts to the existing age recipient, verifies local decryption/content equality and prints only a sanitized receipt. If SOPS is absent on Linux amd64, a private copy of3.13.3 is downloaded and compared with its pinned binary SHA256. Existing separate age identity remains unchanged.

Recipient preflight uses the hash-pinned encrypted source before cluster reads. It parses all public age recipients inside SOPS JSON or standard block-YAML metadata, including quoted keys and indentless age lists. Missing/invalid metadata and duplicate JSON keys stop. A source with several recipients is supported: the existing single X25519 identity is passed to `age-keygen -y` through stdin, and exactly one matching source recipient is selected. The new expanded checkpoint is encrypted only to that operator key, without changing the original cipher or sharing it with the other source recipients. Zero/ambiguous key matches stop before capture. No private identity appears in argv, raw errors, the checkpoint or the repository. Reuse an existing age-keygen or supply --age-keygen /trusted/binary; otherwise a private Linux-amd641.3.2 copy is extracted from its pinned official release archive. No key is generated. The receipt records format/source-recipient count/key match/policy and encryption/archive attempts. For a prior ONE_EXISTING_AGE_RECIPIENT_REQUIRED stop with no archive, use backup --resume-from /protected/previous-checkpoint. Other failures or an existing encrypted archive require reconciliation rather than blind restart. In-memory configuration from failed attempts must be captured for the first complete archive; completed runtime tests are not repeated.

If a recipient stop remains unresolved, use `diagnose --contract /protected/contract.json --resume-from /protected/previous-checkpoint --output-parent /protected/diagnostics`. This mode reports metadata counts/scalar styles only without a key/SOPS/API call. YAML output is textual structure, not semantic or crypto acceptance. Its strict-single-recipient reference verdict is diagnostic; multi-recipient backup uses the existing-key match above. Never select the first recipient or every source recipient implicitly. PVC disposition and Plan-input discovery remain independent.

Run `inputs --contract /protected/contract.json --kubeconfig /protected/personal-kubeconfig --output-parent /protected/diagnostics` independently. It checks the existing local target/trust configuration, reads only the exact PVs named by the accepted inventory, and catalogs existing local variable/backend/State candidates and bounded project Terraform roots. It does not list PVCs/Secrets again, execute Router commands, query credential validity, pull remote State or run Terraform/AWS/Plan. Authentication failure skips remaining PV requests while local input discovery continues. Reclaim/claim-UID information is not a volume-data backup; local State/backend metadata is not a current remote-State acceptance. Catalog truncation and missing/ambiguous source/inputs must remain explicit.

`python3 scripts/prepare-rosa-checkpoint.py verify --archive /independent/checkpoint.sops.json --archive-sha256 <received-sha256> --key-file /separate/age-identity.txt --sops /trusted/sops --output-parent /independent/verification`

Copy only the encrypted archive, sanitized receipt and pinned verification tool to storage outside the Controller/lab failure domain. Transfer the age identity through its separately accepted path, never inside the archive. On Windows use an independently verified SOPS executable and a protected identity file. Verify archive SHA256, real decryption and every stored file hash. The verifier does not extract old files or apply manifests. Different hostnames alone do not prove independent storage; storage owner/access acceptance is additional evidence. The tool intentionally never outputs deletion_ready=true.

`python3 scripts/rosa-lifecycle-plan.py plan --action delete --tf-root /reviewed/source/terraform/rosa --tf-data /existing/tf-data --var-file /protected/current.tfvars.json --cluster-id <current-cluster-id> --output-parent /protected/plans`

Supply the existing purpose AWS session and RHCS_TOKEN through their accepted helpers, not through Git or command-line values. The plan helper requires the initialized S3 rosa Backend key, expected account and seokpan-tf-rosa role, reads current remote State, saves its lineage/serial and prepares a full destroy Plan. Only the exact rosa resource type/name pairs are allowed; Foundation/bootstrap, module changes, mixed changes, replacements or another cluster are rejected. Source/Lock/input/plan hashes are bound in a private manifest. Plan logs/JSON/State may contain protected values: encrypt and independently verify these artifacts as a checkpoint supplement before approving deletion. State locking during Plan is not a resource deletion. No init, state import/removal, target shortcut or Foundation destroy is performed.

For recreation use `--action create --cluster-id NEW:<cluster_name>` with fresh reviewed inputs and `worker_sg_binding=null`. Existing cluster State blocks recreation. Worker SG binding is a second stage after fresh Worker/Machine ownership is observed; an old SG must not be copied from the archive into new-cluster inputs. Old saved Apply plans are history and must not be replayed.

After Plan, run `prepare-rosa-checkpoint.py seal --contract /protected/contract.json --seal-dir /protected/plan-result --key-file /separate/age-identity.txt --output-parent /protected/checkpoints`. Only the Plan/State/receipt/log allowlist is encrypted and locally decrypted; unrelated identities are not copied. This supplement uses the same existing-key match, is copied separately from the identity, and requires the independent real decrypt/hash verification. A failed-Plan receipt may also be sealed to preserve partial diagnostic/State evidence; sealing does not turn it into an accepted Plan. Presence of a captured State is distinct from State ownership/freshness review.

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

### Missing-input supplement

`python3 scripts/collect-rosa-restoration-gaps.py --archive /protected/config.sops.json --archive-sha256 RECEIVED_SHA --key-file /separate/keys.txt --admin-kubeconfig /existing/admin-kubeconfig --admin-credentials /existing/initial-admin.json --contract /protected/contract.json --output-parent /protected/evidence`

This new task decrypts the accepted archive in memory to select missing inputs and build the restoration inventory. It does not repeat the completed independent verification or recreate the original config/Plan archives. It uses an isolated copy of the existing emergency kubeconfig, refreshing only that existing identity through a hidden PTY password prompt if authentication has expired. It creates no administrator or permission grant. Failures stop without resetting credentials or retrying denied reads.

The encrypted supplement captures the previously inaccessible selected OAuth client Secret, non-token GitOps Secrets and config, OperatorGroup/Subscription/CSV, namespaced roles/bindings/accounts and exact cluster roles referenced by GitOps SA bindings. Original objects already in the accepted archive are reused. It reads only the four previously accepted monitoring PVs to bind CSI EBS IDs to claim UID/name/namespace and records monitoring StatefulSet declarations. No volume bytes or AWS snapshots are captured; the resulting volume plan explicitly retains `data_preserved=false`. Snapshot consistency, retained snapshot IDs, restoration mapping and data-read acceptance remain separate.

The object inventory distinguishes protected manual inputs, operator/token regeneration, pinned bootstrap, fresh metadata, new OAuth callback mapping and held manual Applications. Active Cloud workloads or automated Applications are rejected rather than silently changed. The inventory is not an execution driver and does not set restored=true. The new encrypted supplement also requires its own external copy; the existing two independent decrypt results remain accepted.

The accepted first external copy contains the config archive and Plan/State supplement, with real decryption on a host outside the Controller and ROSA. It establishes that storage/access route, not offsite disaster independence. Another Windows copy is optional additional preservation, not a prerequisite after this accepted external copy. Deletion remains unapproved while manual-input classification, volume-data preservation and the held restoration driver/config are incomplete.

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
