variable "enable_rosa_monitoring_preservation" {
  description = "Enable only the reviewed monitoring snapshot permission increment."
  type        = bool
  default     = false
}

variable "rosa_monitoring_preservation_gap_sha256" {
  description = "SHA256 of the accepted protected missing-input archive."
  type        = string
  default     = null
  validation {
    condition     = var.rosa_monitoring_preservation_gap_sha256 == null || can(regex("^[0-9a-f]{64}$", var.rosa_monitoring_preservation_gap_sha256))
    error_message = "Supply the accepted archive SHA256."
  }
}

variable "rosa_monitoring_preservation_sources" {
  description = "Protected map of exactly four captured monitoring EBS IDs to PVC UIDs."
  type        = map(string)
  default     = {}
  sensitive   = true
  validation {
    condition = length(var.rosa_monitoring_preservation_sources) == 0 || (
      length(var.rosa_monitoring_preservation_sources) == 4 &&
      length(toset(values(var.rosa_monitoring_preservation_sources))) == 4 &&
      alltrue([for id, uid in var.rosa_monitoring_preservation_sources :
        can(regex("^vol-[0-9a-f]+$", id)) && can(regex("^[0-9a-f-]{36}$", uid))
      ])
    )
    error_message = "Exactly four distinct captured EBS/PVC identities are required."
  }
}

locals {
  rosa_monitoring_preservation_run = var.rosa_monitoring_preservation_gap_sha256 == null ? "INPUT_REQUIRED" : "rosa-${substr(sha256(var.rosa_monitoring_preservation_gap_sha256), 0, 16)}"
  rosa_monitoring_snapshot_arn     = "arn:aws:ec2:ap-northeast-2::snapshot/*"
}

data "aws_iam_policy_document" "tf_rosa_monitoring_preservation" {
  count = var.enable_rosa_monitoring_preservation ? 1 : 0

  statement {
    sid       = "ReadMonitoringVolumeAndSnapshotMetadata"
    actions   = ["ec2:DescribeVolumes", "ec2:DescribeSnapshots"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
  }
  statement {
    sid       = "SnapshotOnlyFourCapturedVolumes"
    actions   = ["ec2:CreateSnapshot"]
    resources = sort([for id in keys(var.rosa_monitoring_preservation_sources) : "arn:aws:ec2:ap-northeast-2:${local.account_id}:volume/${id}"])
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
    condition {
      test     = "Bool"
      variable = "ec2:Encrypted"
      values   = ["true"]
    }
  }
  statement {
    sid       = "CreateOnlyTaggedMonitoringSnapshots"
    actions   = ["ec2:CreateSnapshot"]
    resources = [local.rosa_monitoring_snapshot_arn]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/seokpan-preservation-run"
      values   = [local.rosa_monitoring_preservation_run]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/seokpan-preservation-scope"
      values   = ["rosa-monitoring"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/seokpan-source-pvc-uid"
      values   = sort(values(var.rosa_monitoring_preservation_sources))
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["seokpan-preservation-run", "seokpan-preservation-scope", "seokpan-source-pvc-uid"]
    }
  }
  statement {
    sid       = "TagOnlyDuringSnapshotCreation"
    actions   = ["ec2:CreateTags"]
    resources = [local.rosa_monitoring_snapshot_arn]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["CreateSnapshot"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/seokpan-preservation-run"
      values   = [local.rosa_monitoring_preservation_run]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/seokpan-preservation-scope"
      values   = ["rosa-monitoring"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/seokpan-source-pvc-uid"
      values   = sort(values(var.rosa_monitoring_preservation_sources))
    }
  }
}

resource "aws_iam_role_policy" "tf_rosa_monitoring_preservation" {
  count  = var.enable_rosa_monitoring_preservation ? 1 : 0
  name   = "seokpan-tf-rosa-monitoring-preservation"
  role   = aws_iam_role.tf["rosa"].id
  policy = data.aws_iam_policy_document.tf_rosa_monitoring_preservation[0].json
  lifecycle {
    precondition {
      condition     = length(var.rosa_monitoring_preservation_sources) == 4 && var.rosa_monitoring_preservation_gap_sha256 != null
      error_message = "The reviewed four-volume source map and archive hash are required."
    }
  }
}
