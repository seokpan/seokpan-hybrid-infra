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

  statement {
    sid = "ManageCiBoundaryPolicy"
    actions = [
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:TagPolicy",
      "iam:UntagPolicy",
      "iam:ListPolicyTags",
    ]
    resources = ["arn:aws:iam::${local.account_id}:policy/seokpan-fnd-ci-boundary"]
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

# 작성자: 최유준
# 작성 날짜: 2026/10/02
# ---------------------------------------------------------------------------
# foundation Role 추가 권한: Registry(ECR) + CI IAM User 관리 (03 §3-C.4, §3-C.11)
# - 대상: foundation Root의 registry.tf(ECR Repository) / ci_iam.tf(CI IAM User)
# - ECR: 이름 접두사 seokpan-fnd-* Repository만 관리 (Lifecycle 관리, Repository Policy는 조회만)
# - IAM User: seokpan-fnd-ci 한 개만 관리, Permissions Boundary(seokpan-fnd-ci-boundary) 지정 필수
# - 의도적으로 제외한 Action (Terraform이 CI 자격증명을 만들지 않도록 함):
#     iam:CreateAccessKey, iam:CreateLoginProfile, iam:AttachUserPolicy, iam:PassRole
#   (CI Access Key는 Terraform 밖에서 발급하여 SOPS+age Automation-CI 번들로 보관)
# - 실제 plan/apply 중 AccessDenied가 나는 Action만 이 블록에 추가 (기본값을 넓게 잡지 않음)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "tf_foundation_registry_ci" {
  statement {
    sid = "ManageSeokpanEcrRepositories"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:DescribeRepositories",
      "ecr:PutImageTagMutability",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutLifecyclePolicy",
      "ecr:GetLifecyclePolicy",
      "ecr:DeleteLifecyclePolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:ListTagsForResource",
      "ecr:TagResource",
      "ecr:UntagResource",
    ]
    resources = ["arn:aws:ecr:ap-northeast-2:${local.account_id}:repository/seokpan-fnd-*"]
  }

  statement {
    sid = "ManageSeokpanCiIamUser"
    actions = [
      "iam:DeleteUser",
      "iam:GetUser",
      "iam:TagUser",
      "iam:UntagUser",
      "iam:ListUserTags",
      "iam:PutUserPolicy",
      "iam:GetUserPolicy",
      "iam:DeleteUserPolicy",
      "iam:ListUserPolicies",
      "iam:ListAttachedUserPolicies",
      "iam:ListGroupsForUser",
    ]
    resources = ["arn:aws:iam::${local.account_id}:user/seokpan-fnd-ci"]
  }

  # Boundary(seokpan-fnd-ci-boundary)를 지정한 경우에만 User 생성/Boundary 설정 허용
  statement {
    sid       = "CreateCiUserOnlyWithBoundary"
    actions   = ["iam:CreateUser", "iam:PutUserPermissionsBoundary"]
    resources = ["arn:aws:iam::${local.account_id}:user/seokpan-fnd-ci"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.ci_boundary.arn]
    }
  }

  # Boundary 제거 금지
  statement {
    sid       = "DenyRemoveCiUserBoundary"
    effect    = "Deny"
    actions   = ["iam:DeleteUserPermissionsBoundary"]
    resources = ["arn:aws:iam::${local.account_id}:user/seokpan-fnd-ci"]
  }
}

# 작성자: 최유준
# 작성 날짜: 2026/10/02
# ---------------------------------------------------------------------------
# CI IAM User(seokpan-fnd-ci) Permissions Boundary
# - CI User의 identity-based 권한(User/inline policy)이 가질 수 있는 최대 범위를 ECR push/pull로 고정
# - resource-based policy(ECR Repository Policy)의 직접 grant에는 적용되지 않으므로,
#   foundation Role에는 ecr:SetRepositoryPolicy를 부여하지 않음
# - bootstrap이 소유: foundation Role은 이 Policy를 수정/삭제할 수 없음
# - apply 순서: bootstrap Role의 ManageCiBoundaryPolicy 권한(tf_bootstrap) 적용 후 생성
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ci_boundary" {
  statement {
    sid       = "EcrAuthToken"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPushPullSeokpanRepositories"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:DescribeImages",
    ]
    resources = ["arn:aws:ecr:ap-northeast-2:${local.account_id}:repository/seokpan-fnd-*"]
  }
}

resource "aws_iam_policy" "ci_boundary" {
  name        = "seokpan-fnd-ci-boundary"
  description = "Permissions boundary for CI IAM user seokpan-fnd-ci (ECR push/pull only)"
  policy      = data.aws_iam_policy_document.ci_boundary.json

  tags = {
    Component = "ci"
  }

  # bootstrap Role이 먼저 CreatePolicy 등 권한을 갖도록 순서 고정
  depends_on = [aws_iam_role_policy.tf_bootstrap]
}

resource "aws_iam_role_policy" "tf_foundation_registry_ci" {
  name   = "seokpan-tf-foundation-registry-ci"
  role   = aws_iam_role.tf["foundation"].id
  policy = data.aws_iam_policy_document.tf_foundation_registry_ci.json
}

output "tf_exec_role_arns" {
  description = "Root별 Terraform 실행 Role ARN"
  value       = { for k, r in aws_iam_role.tf : k => r.arn }
}
