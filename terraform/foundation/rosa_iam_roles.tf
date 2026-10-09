# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA 공통 역할 4개와 공식 정책 연결 선언 (#47)

# Installer 신뢰 대상은 검증한 구성표의 참조를 사용합니다.
# Support 신뢰 정책은 검증한 공식 파일을 그대로 사용합니다.
# ControlPlane·Worker는 공식 코드의 EC2 서비스 신뢰를 사용합니다.
# 실제 AWS 자원·신뢰 대상·State 소유권 확인은 적용 전 별도 수행합니다.
# 추가 External ID 조건이 필요한 구성은 적용 전 소비 입력과 재대조합니다.

locals {
  rosa_installer_trust_principal = (
    var.enable_rosa_account_iam
    ? try(
      tostring(local.rosa_bundle_manifest.installer_trust_reference.principal),
      ""
    )
    : ""
  )

  rosa_support_trust_document = (
    var.enable_rosa_account_iam
    ? try(
      jsondecode(file(
        "${local.rosa_bundle_directory}/sts_support_trust_policy.json"
      )),
      null
    )
    : null
  )

  rosa_support_trust_principals = (
    var.enable_rosa_account_iam
    ? try(
      tolist(local.rosa_support_trust_document.Statement[0].Principal.AWS),
      [tostring(local.rosa_support_trust_document.Statement[0].Principal.AWS)],
      []
    )
    : []
  )

  rosa_ec2_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })

  rosa_account_trust_policies = (
    var.enable_rosa_account_iam
    ? {
      installer = jsonencode({
        Version = "2012-10-17"
        Statement = [{
          Effect = "Allow"
          Action = "sts:AssumeRole"
          Principal = {
            AWS = local.rosa_installer_trust_principal
          }
        }]
      })
      support = file(
        "${local.rosa_bundle_directory}/sts_support_trust_policy.json"
      )
      controlplane = local.rosa_ec2_trust_policy
      worker       = local.rosa_ec2_trust_policy
    }
    : {}
  )
}

resource "aws_iam_role" "rosa_account" {
  for_each = local.rosa_account_trust_policies

  name = "${var.rosa_account_role_prefix}-${local.rosa_account_role_properties[each.key].name}-Role"
  path = var.rosa_iam_path

  assume_role_policy   = each.value
  permissions_boundary = var.rosa_account_permissions_boundary

  tags = {
    red-hat-managed        = "true"
    rosa_openshift_version = var.rosa_iam_openshift_minor_version
    rosa_role_prefix       = var.rosa_account_role_prefix
    rosa_role_type         = local.rosa_account_role_properties[each.key].role_type
  }

  depends_on = [
    terraform_data.rosa_policy_bundle_guard
  ]

  lifecycle {
    precondition {
      condition     = var.rosa_iam_openshift_minor_version != null
      error_message = "역할 생성 전에 확인한 OpenShift 버전 계열을 공급해야 합니다."
    }

    precondition {
      condition = (
        each.key != "installer" ||
        can(regex(
          "^arn:aws:iam::[0-9]{12}:role/RH-Managed-OpenShift-Installer$",
          local.rosa_installer_trust_principal
        ))
      )
      error_message = "구성표의 Installer 신뢰 대상이 공식 역할 이름 형식과 다릅니다."
    }

    precondition {
      condition = (
        each.key != "support" ||
        try(
          length(local.rosa_support_trust_document.Statement) == 1 &&
          length(local.rosa_support_trust_principals) == 1 &&
          contains(
            local.rosa_support_trust_principals,
            local.rosa_bundle_manifest.support_trust_principal
          ),
          false
        )
      )
      error_message = "Support 신뢰 정책의 대상이 구성표의 수신 기록과 일치하지 않습니다."
    }
  }
}

resource "aws_iam_role_policy_attachment" "rosa_account" {
  for_each = aws_iam_role.rosa_account

  role       = each.value.name
  policy_arn = aws_iam_policy.rosa_account[each.key].arn
}
