variable "enable_rosa_monitoring_restore" {
  type    = bool
  default = false
}

variable "rosa_monitoring_restore_policy_json" {
  type      = string
  sensitive = true
  default   = null
}

locals {
  rosa_monitoring_restore_policy_name = "seokpan-tf-rosa-monitoring-restore"
  rosa_monitoring_restore_policy_arn  = "arn:aws:iam::${local.account_id}:policy/${local.rosa_monitoring_restore_policy_name}"
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
  policy     = var.rosa_monitoring_restore_policy_json
  tags       = { Component = "rosa-monitoring-restore" }
  depends_on = [aws_iam_role_policy.tf_bootstrap]
  lifecycle {
    precondition {
      condition     = var.rosa_monitoring_restore_policy_json == null ? false : (can(jsondecode(var.rosa_monitoring_restore_policy_json)) && length(var.rosa_monitoring_restore_policy_json) <= 6144)
      error_message = "Supply the exact reviewed four-snapshot restoration policy within the managed-policy limit."
    }
  }
}

resource "aws_iam_role_policy_attachment" "tf_rosa_monitoring_restore" {
  count      = var.enable_rosa_monitoring_restore ? 1 : 0
  role       = aws_iam_role.tf["rosa"].name
  policy_arn = local.rosa_monitoring_restore_policy_arn
  depends_on = [aws_iam_policy.tf_rosa_monitoring_restore]
}
