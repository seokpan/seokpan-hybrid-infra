# Monitoring restoration IAM scope

`terraform/bootstrap/rosa_monitoring_restore.tf` generates the managed policy
from `rosa_monitoring_restore_sources`, a sensitive map of exactly four distinct
Snapshot IDs to their source KMS key ARNs. It does not use externally supplied
policy JSON as the managed-policy content.

The generated six statements allow only:

- `ec2:CreateVolume` on the four Snapshot ARNs and same-account Seoul volume ARNs.
  The new volume must be encrypted, use an exact `ec2:ParentSnapshot`, and carry
  both restoration-identifying request tags.
- `ec2:CreateTags` on those volume ARNs only during `CreateVolume`.
- Metadata reads on the exact source KMS keys.
- Decrypt, data-key-without-plaintext and re-encryption operations on those keys
  through `ec2.ap-northeast-2.amazonaws.com`.
- Grants on those keys through the same EC2 service, with
  `kms:GrantIsForAWSResource=true`.

Snapshot syntax/count, concrete Seoul KMS key syntax and the current Bootstrap
account are checked in Terraform. The protected execution helper additionally
binds this map to the accepted completed-snapshot journal. The map is an input
to a bounded generator; it is not proof that a Snapshot exists, is completed,
belongs to a volume with a matching key, or can actually be restored. Those
runtime facts retain their separate acceptance evidence.

The legacy `rosa_monitoring_restore_policy_json` input is optional and retained
only as an assertion for the reviewed helper. If supplied, it must be equivalent
to the generated policy. Statement order, Action/Resource order and IAM scalar
versus list serialization are normalized. Extra statements/fields, wildcard
scope and removed conditions are rejected. The resource always uses the
Terraform-generated document, even when this assertion is omitted.

`rosa-monitoring-restore-permission.py` passes both the protected structured map
and its existing exact-policy assertion. Reconciliation binds the same map to
the new remainder inputs, preserving the old saved inputs/Plan/Apply marker.
An old partial run with a different pinned HCL is stopped for source review; it
is not silently rewritten or replayed. Already accepted IAM application and
permission prerequisites remain historical results. This source correction is
not an instruction to apply that policy again.

## Source verification

`scripts/test_rosa_restore_terraform_scope.py` uses Terraform 1.16.4 and the real
AWS 6.67.0 `aws_iam_policy_document` generator in a disposable synthetic root.
The committed provider lock is used read-only; a local offline platform mirror
may supply its existing platform-appropriate lock with `--provider-lock-file`.
Initialization may download only the pinned provider. Subsequent synthetic
Plans use dummy credentials, disable metadata/credential/account checks, block
HTTP(S) via a local closed proxy, do not refresh State and never Apply.

The 19 cases cover generator/legacy equivalence and rejection of Action or
Resource wildcards, an extra Snapshot/key/statement, removed ParentSnapshot,
encryption, request tags, CreateTags action, ViaService or service-grant
conditions, non-four source counts and foreign account/region or wildcard keys.
Positive generated documents are compared with the existing Python exact
policy builder. `test_rosa_restore_structured_inputs.py` adds six synthetic
helper-input and partial-run preservation checks, plus Snapshot shape parity.

No AWS permission change, Snapshot creation, decryption, production Plan,
ROSA creation/deletion, SQL or application execution is part of these tests.
