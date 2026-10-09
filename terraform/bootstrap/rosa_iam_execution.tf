# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: Foundation의 ROSA 공통 IAM 관리 권한 선언 (#47)

# 실제 적용 전 전체 검토·실행 승인을 거칩니다.
# 승인한 접두사 seokpan-fnd-rosa와 IAM 경로 /를 대상으로 합니다.
# 접두사·경로 변경 시 Foundation 선언과 함께 다시 대조합니다.
# 현재 권한 경계 미지정 구성만 지원합니다.
# 권한 경계가 필요하면 승인한 경계와 조건을 추가로 검토합니다.
# 클러스터 전용 Operator 역할·OIDC·PassRole은 이 권한의 대상이 아닙니다.

variable "enable_rosa_account_iam_permissions" {
  description = "Foundation의 ROSA 공통 IAM 관리 권한 생성 여부입니다."
  type        = bool
  default     = false
  nullable    = false
}

locals {
  rosa_execution_role_names = {
    installer    = "seokpan-fnd-rosa-Installer-Role"
    support      = "seokpan-fnd-rosa-Support-Role"
    controlplane = "seokpan-fnd-rosa-ControlPlane-Role"
    worker       = "seokpan-fnd-rosa-Worker-Role"
  }

  rosa_execution_role_arns = {
    for key, name in local.rosa_execution_role_names :
    key => "arn:aws:iam::${local.account_id}:role/${name}"
  }

  rosa_execution_account_policy_arns = {
    for key, name in local.rosa_execution_role_names :
    key => "arn:aws:iam::${local.account_id}:policy/${name}-Policy"
  }

  rosa_execution_operator_policy_names = [
    "seokpan-fnd-rosa-openshift-cloud-credential-operator-cloud-crede",
    "seokpan-fnd-rosa-openshift-cloud-network-config-controller-cloud",
    "seokpan-fnd-rosa-openshift-cluster-csi-drivers-ebs-cloud-credent",
    "seokpan-fnd-rosa-openshift-image-registry-installer-cloud-creden",
    "seokpan-fnd-rosa-openshift-ingress-operator-cloud-credentials",
    "seokpan-fnd-rosa-openshift-machine-api-aws-cloud-credentials",
  ]

  rosa_execution_policy_arns = concat(
    values(local.rosa_execution_account_policy_arns),
    [
      for name in local.rosa_execution_operator_policy_names :
      "arn:aws:iam::${local.account_id}:policy/${name}"
    ]
  )

  rosa_execution_management_policy_arn = "arn:aws:iam::${local.account_id}:policy/seokpan-tf-foundation-rosa-iam"
}

data "aws_iam_policy_document" "tf_foundation_rosa_iam" {
  statement {
    sid = "ReadRosaAccountRoles"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRoleTags",
    ]
    resources = values(local.rosa_execution_role_arns)
  }

  statement {
    sid       = "CreateRosaAccountRoles"
    actions   = ["iam:CreateRole"]
    resources = values(local.rosa_execution_role_arns)

    condition {
      test     = "Null"
      variable = "iam:PermissionsBoundary"
      values   = ["true"]
    }
  }

  statement {
    sid = "ManageRosaAccountRoles"
    actions = [
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
    ]
    resources = values(local.rosa_execution_role_arns)
  }

  statement {
    sid = "ManageRosaOfficialPolicies"
    actions = [
      "iam:CreatePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:CreatePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:DeletePolicy",
      "iam:TagPolicy",
      "iam:UntagPolicy",
      "iam:ListPolicyTags",
    ]
    resources = local.rosa_execution_policy_arns
  }

  dynamic "statement" {
    for_each = local.rosa_execution_role_names

    content {
      sid = "Attach${replace(statement.value, "-", "")}Policy"
      actions = [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
      ]
      resources = [
        local.rosa_execution_role_arns[statement.key]
      ]

      condition {
        test     = "ArnEquals"
        variable = "iam:PolicyARN"
        values = [
          local.rosa_execution_account_policy_arns[statement.key]
        ]
      }
    }
  }
}

# bootstrap은 관리용 정책 한 개만 생성·관리하고,
# Foundation 실행 역할에만 연결합니다.
data "aws_iam_policy_document" "tf_bootstrap_rosa_iam" {
  statement {
    sid = "ManageFoundationRosaIamPolicy"
    actions = [
      "iam:CreatePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:CreatePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:DeletePolicy",
      "iam:TagPolicy",
      "iam:UntagPolicy",
      "iam:ListPolicyTags",
    ]
    resources = [
      local.rosa_execution_management_policy_arn
    ]
  }

  statement {
    sid = "AttachFoundationRosaIamPolicy"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/seokpan-tf-foundation"
    ]

    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values = [
        local.rosa_execution_management_policy_arn
      ]
    }
  }
}

resource "aws_iam_policy" "tf_foundation_rosa_iam" {
  count = var.enable_rosa_account_iam_permissions ? 1 : 0

  name   = "seokpan-tf-foundation-rosa-iam"
  policy = data.aws_iam_policy_document.tf_foundation_rosa_iam.json

  tags = {
    Component = "tf-exec-role"
  }

  depends_on = [
    aws_iam_role_policy.tf_bootstrap
  ]

  lifecycle {
    precondition {
      condition = length(jsonencode(jsondecode(
        data.aws_iam_policy_document.tf_foundation_rosa_iam.json
      ))) <= 6144
      error_message = "관리형 정책의 크기가 6144자 제한을 초과합니다."
    }
  }
}

resource "aws_iam_role_policy_attachment" "tf_foundation_rosa_iam" {
  count = var.enable_rosa_account_iam_permissions ? 1 : 0

  role       = aws_iam_role.tf["foundation"].name
  policy_arn = aws_iam_policy.tf_foundation_rosa_iam[0].arn
}
