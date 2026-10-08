# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA 공통 역할 권한 정책 4개와 Operator 정책 6개 선언 (#47)

# 기본 생성 설정은 기존 enable_rosa_account_iam의 false를 따릅니다.
# 공식 정책 파일의 권한 내용을 임의로 수정하지 않습니다.
# 역할 생성·신뢰 정책·역할과 정책의 연결은 후속 파일에서 작성합니다.

locals {
  rosa_account_role_properties = {
    installer = {
      name      = "Installer"
      role_type = "installer"
    }
    support = {
      name      = "Support"
      role_type = "support"
    }
    controlplane = {
      name      = "ControlPlane"
      role_type = "instance_controlplane"
    }
    worker = {
      name      = "Worker"
      role_type = "instance_worker"
    }
  }
}

resource "aws_iam_policy" "rosa_account" {
  for_each = (
    var.enable_rosa_account_iam
    ? local.rosa_account_permission_policy_files
    : {}
  )

  name = "${var.rosa_account_role_prefix}-${local.rosa_account_role_properties[each.key].name}-Role-Policy"
  path = var.rosa_iam_path

  policy = file(
    "${local.rosa_bundle_directory}/${each.value}"
  )

  tags = {
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
      error_message = "공통 IAM 생성 전에 확인한 OpenShift 버전 계열을 공급해야 합니다."
    }
  }
}

resource "aws_iam_policy" "rosa_operator" {
  for_each = (
    var.enable_rosa_account_iam
    ? local.rosa_operator_permission_policy_files
    : {}
  )

  # 태훈 님이 전달한 소비 이름을 그대로 사용합니다.
  name = each.key
  path = var.rosa_iam_path

  policy = file(
    "${local.rosa_bundle_directory}/${each.value}"
  )

  depends_on = [
    terraform_data.rosa_policy_bundle_guard
  ]

  lifecycle {
    precondition {
      condition     = length(each.key) <= 64
      error_message = "Operator 정책 이름은 AWS IAM 정책 이름의 64자 제한을 지켜야 합니다."
    }
  }
}
