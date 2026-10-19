variable "enable_rosa_monitoring_restore" {
  type    = bool
  default = false
}

variable "rosa_monitoring_restore_policy_json" {
  description = "Optional compatibility assertion only; the managed policy is generated from the structured sources below."
  type        = string
  sensitive   = true
  default     = null
}

variable "rosa_monitoring_restore_sources" {
  description = "Exactly four reviewed Snapshot IDs mapped to their source KMS key ARNs."
  type        = map(string)
  sensitive   = true
  default     = {}
  validation {
    condition = !var.enable_rosa_monitoring_restore || (
      length(var.rosa_monitoring_restore_sources) == 4 && alltrue([
        for snapshot, key in var.rosa_monitoring_restore_sources :
        can(regex("^snap-([0-9a-f]{8}|[0-9a-f]{17})$", snapshot)) &&
        can(regex("^arn:aws:kms:ap-northeast-2:[0-9]{12}:key/[a-zA-Z0-9-]+$", key))
      ])
    )
    error_message = "Restoration requires exactly four distinct Snapshot IDs and concrete Seoul KMS key ARNs."
  }
}

locals {
  rosa_monitoring_restore_policy_name = "seokpan-tf-rosa-monitoring-restore"
  rosa_monitoring_restore_policy_arn  = "arn:aws:iam::${local.account_id}:policy/${local.rosa_monitoring_restore_policy_name}"
  rosa_monitoring_restore_snapshots   = sort([for snapshot in keys(var.rosa_monitoring_restore_sources) : "arn:aws:ec2:ap-northeast-2::snapshot/${snapshot}"])
  rosa_monitoring_restore_keys        = sort(distinct(values(var.rosa_monitoring_restore_sources)))
  rosa_monitoring_restore_volume_arn  = "arn:aws:ec2:ap-northeast-2:${local.account_id}:volume/*"

  # Compare IAM semantics without weakening any statement or condition. Extra
  # fields remain in the comparison; duplicate Sids or malformed documents fail.
  rosa_monitoring_restore_documents = {
    expected = try(jsondecode(data.aws_iam_policy_document.tf_rosa_monitoring_restore[0].json), null)
    supplied = try(jsondecode(var.rosa_monitoring_restore_policy_json), null)
  }
  rosa_monitoring_restore_normalized = {
    for name, document in local.rosa_monitoring_restore_documents : name => try(merge(document, {
      Statement = { for statement in document.Statement : statement.Sid => merge(statement, {
        Action   = sort(flatten([statement.Action]))
        Resource = sort(flatten([statement.Resource]))
        Condition = { for operator, entries in try(statement.Condition, {}) : operator => {
          for key, value in entries : key => sort([for item in flatten([value]) : tostring(item)])
        } }
      }) }
    }), null)
  }
}

data "aws_iam_policy_document" "tf_rosa_monitoring_restore" {
  count = var.enable_rosa_monitoring_restore ? 1 : 0
  statement {
    sid       = "UseExactSnapshots"
    actions   = ["ec2:CreateVolume"]
    resources = local.rosa_monitoring_restore_snapshots
  }
  statement {
    sid       = "CreateOnlyEncryptedSnapshotRestorations"
    actions   = ["ec2:CreateVolume"]
    resources = [local.rosa_monitoring_restore_volume_arn]
    condition {
      test     = "ArnEquals"
      variable = "ec2:ParentSnapshot"
      values   = local.rosa_monitoring_restore_snapshots
    }
    condition {
      test     = "Bool"
      variable = "ec2:Encrypted"
      values   = ["true"]
    }
    condition {
      test     = "Null"
      variable = "aws:RequestTag/seokpan-restored-cluster"
      values   = ["false"]
    }
    condition {
      test     = "Null"
      variable = "aws:RequestTag/seokpan-source-snapshot"
      values   = ["false"]
    }
  }
  statement {
    sid       = "TagOnlyDuringVolumeCreation"
    actions   = ["ec2:CreateTags"]
    resources = [local.rosa_monitoring_restore_volume_arn]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["CreateVolume"]
    }
  }
  statement {
    sid       = "ReadExactKeyMetadata"
    actions   = ["kms:DescribeKey"]
    resources = local.rosa_monitoring_restore_keys
  }
  statement {
    sid       = "UseExactKeysThroughEc2"
    actions   = ["kms:Decrypt", "kms:GenerateDataKeyWithoutPlaintext", "kms:ReEncrypt*"]
    resources = local.rosa_monitoring_restore_keys
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.ap-northeast-2.amazonaws.com"]
    }
  }
  statement {
    sid       = "ServiceResourceGrantsOnly"
    actions   = ["kms:CreateGrant"]
    resources = local.rosa_monitoring_restore_keys
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.ap-northeast-2.amazonaws.com"]
    }
    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values   = ["true"]
    }
  }
}

data "aws_iam_policy_document" "tf_bootstrap_monitoring_restore" {
  count = var.enable_rosa_monitoring_restore ? 1 : 0
  statement {
    sid = "ManageExactRosaMonitoringRestorePolicy"
    actions = [
      "iam:CreatePolicy", "iam:DeletePolicy", "iam:GetPolicy", "iam:GetPolicyVersion",
      "iam:ListPolicyVersions", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion",
      "iam:TagPolicy", "iam:UntagPolicy", "iam:ListPolicyTags"
    ]
    resources = [local.rosa_monitoring_restore_policy_arn]
  }
  statement {
    sid       = "AttachExactRosaMonitoringRestorePolicy"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = ["arn:aws:iam::${local.account_id}:role/seokpan-tf-rosa"]
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = [local.rosa_monitoring_restore_policy_arn]
    }
  }
}

resource "aws_iam_policy" "tf_rosa_monitoring_restore" {
  count      = var.enable_rosa_monitoring_restore ? 1 : 0
  name       = local.rosa_monitoring_restore_policy_name
  path       = "/"
  policy     = data.aws_iam_policy_document.tf_rosa_monitoring_restore[0].json
  tags       = { Component = "rosa-monitoring-restore" }
  depends_on = [aws_iam_role_policy.tf_bootstrap]
  lifecycle {
    precondition {
      condition = alltrue([
        for key in local.rosa_monitoring_restore_keys :
        startswith(key, "arn:aws:kms:ap-northeast-2:${local.account_id}:key/")
      ])
      error_message = "Restoration keys must belong to the current Bootstrap account in Seoul."
    }
    precondition {
      condition     = length(data.aws_iam_policy_document.tf_rosa_monitoring_restore[0].json) <= 6144
      error_message = "The generated restoration policy must fit the managed-policy limit."
    }
    precondition {
      condition = var.rosa_monitoring_restore_policy_json == null ? true : (
        local.rosa_monitoring_restore_normalized.expected != null &&
        local.rosa_monitoring_restore_normalized.supplied == local.rosa_monitoring_restore_normalized.expected
      )
      error_message = "A supplied legacy policy must exactly match the generated four-snapshot policy, including every KMS and volume condition."
    }
  }
}

resource "aws_iam_role_policy_attachment" "tf_rosa_monitoring_restore" {
  count      = var.enable_rosa_monitoring_restore ? 1 : 0
  role       = aws_iam_role.tf["rosa"].name
  policy_arn = local.rosa_monitoring_restore_policy_arn
  depends_on = [aws_iam_policy.tf_rosa_monitoring_restore]
}
