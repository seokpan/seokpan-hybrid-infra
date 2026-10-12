# 작성자: 이유빈
# 작성일: 2026-10-12
# 작성내용: ROSA 최초 빈 State Plan의 기반 조회 권한 준비 (#25)

# 기존 Backend 정책과 분리한 조회 전용 정책입니다.
# 실제 생성 활성화는 리뷰·전체 bootstrap Plan·승인 후 진행합니다.
# 기존 State Refresh·IAM/OIDC 생성·SG 변경·삭제 권한은 후속 범위입니다.

variable "enable_rosa_plan_read_permissions" {
  description = "ROSA 최초 Plan의 기반 조회 권한 활성화 여부입니다."
  type        = bool
  default     = false
  nullable    = false
}

variable "rosa_plan_foundation_vpc_id" {
  description = "Foundation 적용 후 확인한 실제 VPC ID입니다. Git 밖 입력으로 공급합니다."
  type        = string
  default     = null

  validation {
    condition = (
      var.rosa_plan_foundation_vpc_id == null
      ? true
      : can(regex(
        "^vpc-([0-9a-f]{8}|[0-9a-f]{17})$",
        var.rosa_plan_foundation_vpc_id
      ))
    )
    error_message = "실제 Foundation VPC ID 형식이어야 합니다."
  }
}

data "aws_iam_policy_document" "tf_rosa_first_plan_read" {
  count = (
    var.enable_rosa_plan_read_permissions &&
    var.rosa_plan_foundation_vpc_id != null
  ) ? 1 : 0

  statement {
    sid = "ReadSeoulNetworkLists"
    actions = [
      "ec2:DescribeVpcs",
      "ec2:DescribeSubnets",
      "ec2:DescribeRouteTables",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
  }

  statement {
    sid     = "ReadSuppliedFoundationVpcAttributes"
    actions = ["ec2:DescribeVpcAttribute"]
    resources = [
      "arn:aws:ec2:ap-northeast-2:${local.account_id}:vpc/${var.rosa_plan_foundation_vpc_id}"
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
  }

  statement {
    sid       = "ReadFoundationAccountRoles"
    actions   = ["iam:GetRole"]
    resources = values(local.rosa_execution_role_arns)
  }
}

resource "aws_iam_role_policy" "tf_rosa_first_plan_read" {
  count = var.enable_rosa_plan_read_permissions ? 1 : 0

  name = "seokpan-tf-rosa-first-plan-read"
  role = aws_iam_role.tf["rosa"].id
  policy = try(
    data.aws_iam_policy_document.tf_rosa_first_plan_read[0].json,
    "{}"
  )

  lifecycle {
    precondition {
      condition     = var.rosa_plan_foundation_vpc_id != null
      error_message = "조회 권한 활성화에는 확인한 실제 Foundation VPC ID가 필요합니다."
    }

    precondition {
      condition = length(jsonencode(jsondecode(
        try(
          data.aws_iam_policy_document.tf_rosa_first_plan_read[0].json,
          "{}"
        )
      ))) <= 10240
      error_message = "개별 정책 문서의 크기 제한을 초과합니다. 역할별 전체 합계는 별도로 검사해야 합니다."
    }
  }
}
