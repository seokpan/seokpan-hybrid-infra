# Terraform 실행 Role (03 §3-C.4, §3-F.4.2, §3-F.15)
# - Root(bootstrap / foundation / rosa)별 실행 Role을 bootstrap이 소유
# - 사람 IAM User + MFA 조건으로만 AssumeRole 허용
# - 현재 단계: foundation / rosa Role은 자기 State/Lock 접근만 허용
#   AWS 서비스·IAM·PassRole 권한은 각 Root 구현 PR에서 필요한 Action/Resource만 이 파일에 추가
#   (PassRole은 대상 Role ARN + iam:PassedToService 조건으로 제한, bootstrap apply 후 해당 Root 실행)

locals {
  account_id = data.aws_caller_identity.current.account_id

  # AssumeRole을 허용할 사람 IAM User (자동화용 ECR CI / Backup User는 포함하지 않음)
  tf_trusted_users = ["ksh_data", "tjung", "yb", "cyj2000"]

  # Root별 실행 Role 설정
  tf_roles = {
    bootstrap  = { state_prefix = "phase2/bootstrap", max_session = 3600 }
    foundation = { state_prefix = "phase2/foundation", max_session = 7200 }
    rosa       = { state_prefix = "phase2/rosa", max_session = 14400 }
  }

  # State 버킷 관리 권한이 없는 Root Role
  tf_workload_roles = { for k, v in local.tf_roles : k => v if k != "bootstrap" }
}

# 신뢰 정책: 지정 사람 IAM User가 MFA 인증한 경우에만 AssumeRole
data "aws_iam_policy_document" "tf_trust" {
  statement {
    sid     = "AllowProjectHumansWithMFA"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [for u in local.tf_trusted_users : "arn:aws:iam::${local.account_id}:user/${u}"]
    }

    condition {
      test     = "Bool"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role" "tf" {
  for_each = local.tf_roles

  name                 = "seokpan-tf-${each.key}"
  description          = "Terraform ${each.key} root execution role"
  assume_role_policy   = data.aws_iam_policy_document.tf_trust.json
  max_session_duration = each.value.max_session

  tags = {
    Component = "tf-exec-role"
  }
}

# ---------------------------------------------------------------------------
# bootstrap Role: State 버킷 설정 관리 + TF 실행 Role 관리
# 객체 접근 범위(자기 접두사), State/Version/버킷 삭제 금지는 버킷 정책(main.tf)에서 제한
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "tf_bootstrap" {
  statement {
    sid       = "ManageStateBucket"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
  }

  statement {
    sid = "ManageTfExecRoles"
    actions = [
      "iam:GetRole",
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/seokpan-tf-*"]
  }
}

resource "aws_iam_role_policy" "tf_bootstrap" {
  name   = "seokpan-tf-bootstrap"
  role   = aws_iam_role.tf["bootstrap"].id
  policy = data.aws_iam_policy_document.tf_bootstrap.json
}

# ---------------------------------------------------------------------------
# foundation / rosa Role: 자기 Root의 backend 접근만 허용 (03 §3-F.15.1)
# - State(*.tfstate): Get / Put
# - Lock(*.tflock): Get / Put / Delete
# - List: 자기 접두사만 (backend의 workspace_key_prefix도 자기 접두사 안에 둠)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "tf_backend" {
  for_each = local.tf_workload_roles

  statement {
    sid       = "ListOwnStatePrefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${each.value.state_prefix}/*"]
    }
  }

  statement {
    sid       = "ReadWriteOwnState"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${each.value.state_prefix}/*.tfstate"]
  }

  statement {
    sid       = "ManageOwnLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${each.value.state_prefix}/*.tflock"]
  }
}

resource "aws_iam_role_policy" "tf_backend" {
  for_each = local.tf_workload_roles

  name   = "seokpan-tf-${each.key}-backend"
  role   = aws_iam_role.tf[each.key].id
  policy = data.aws_iam_policy_document.tf_backend[each.key].json
}

output "tf_exec_role_arns" {
  description = "Root별 Terraform 실행 Role ARN"
  value       = { for k, r in aws_iam_role.tf : k => r.arn }
}
