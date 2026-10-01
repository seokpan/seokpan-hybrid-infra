# Terraform 실행 Role (03 §3-C.4, §3-F.4.2, §3-F.15)
# - Root(bootstrap / foundation / rosa)별 실행 Role을 bootstrap이 소유
# - 사람 IAM User + MFA 조건으로만 AssumeRole 허용
# - State 접근 범위는 버킷 정책(main.tf)에서 Role별 key 접두사로 제한

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

  # foundation / rosa Role이 생성·관리할 수 있는 IAM 이름 접두사
  # 초기값이며 각 Root 구현 시 실제 Role 이름에 맞춰 조정 (PR로 리뷰)
  tf_iam_prefixes = {
    foundation = ["seokpan-fnd-", "seokpan-acct-"]
    rosa       = ["seokpan-op-"]
  }
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
# bootstrap Role: State 버킷 관리 + TF 실행 Role 자체 관리
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
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
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
# foundation / rosa Role: AWS 서비스 권한(PowerUserAccess) + 접두사로 제한한 IAM 권한
# PowerUserAccess는 IAM·Organizations를 제외하므로, IAM은 아래 범위만 추가 허용
# State 버킷의 다른 key·버킷 설정 변경은 버킷 정책에서 차단 (main.tf)
# ---------------------------------------------------------------------------
resource "aws_iam_role_policy_attachment" "tf_poweruser" {
  for_each = local.tf_iam_prefixes

  role       = aws_iam_role.tf[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "tf_iam_scoped" {
  for_each = local.tf_iam_prefixes

  statement {
    sid = "ManageScopedRoles"
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
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:PassRole",
    ]
    resources = [for p in each.value : "arn:aws:iam::${local.account_id}:role/${p}*"]
  }

  statement {
    sid = "ManageScopedInstanceProfiles"
    actions = [
      "iam:GetInstanceProfile",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:UntagInstanceProfile",
    ]
    resources = [for p in each.value : "arn:aws:iam::${local.account_id}:instance-profile/${p}*"]
  }

  statement {
    sid = "ManageScopedPolicies"
    actions = [
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = [for p in each.value : "arn:aws:iam::${local.account_id}:policy/${p}*"]
  }

  statement {
    sid       = "ReadIam"
    actions   = ["iam:Get*", "iam:List*"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "tf_iam_scoped" {
  for_each = local.tf_iam_prefixes

  name   = "seokpan-tf-${each.key}-iam-scoped"
  role   = aws_iam_role.tf[each.key].id
  policy = data.aws_iam_policy_document.tf_iam_scoped[each.key].json
}

# rosa Role: Cluster별 OIDC Provider 관리 (Cluster-specific IAM/OIDC는 rosa 소유)
data "aws_iam_policy_document" "tf_rosa_oidc" {
  statement {
    sid = "ManageOidcProvider"
    actions = [
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:AddClientIDToOpenIDConnectProvider",
      "iam:RemoveClientIDFromOpenIDConnectProvider",
    ]
    resources = ["arn:aws:iam::${local.account_id}:oidc-provider/*"]
  }
}

resource "aws_iam_role_policy" "tf_rosa_oidc" {
  name   = "seokpan-tf-rosa-oidc"
  role   = aws_iam_role.tf["rosa"].id
  policy = data.aws_iam_policy_document.tf_rosa_oidc.json
}

output "tf_exec_role_arns" {
  description = "Root별 Terraform 실행 Role ARN"
  value       = { for k, r in aws_iam_role.tf : k => r.arn }
}
