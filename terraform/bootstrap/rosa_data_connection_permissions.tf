# 작성자: 이유빈
# 작성일: 2026-10-14
# 작성 내용: ROSA 실행 역할의 Foundation DB·Valkey 연결 규칙 권한
# 실제 보안 그룹 번호는 Git 밖 보호 입력으로 공급합니다.
# 기존 역할·Trust·조회 권한·설치 권한은 변경하지 않습니다.
# 조회는 서울 리전으로 제한합니다.
# 접속 규칙 생성·삭제는 지정 보안 그룹 두 개로 제한합니다.
# 새 규칙의 태그 권한은 접속 규칙 생성 요청에만 허용합니다.
# 태그 포함 생성 시 규칙 대상 권한은 AWS의 태그 입력 평가에 대응합니다.

variable "enable_rosa_data_connection_permissions" {
  type        = bool
  default     = false
  nullable    = false
  description = "ROSA의 Foundation Data 연결 규칙 권한 활성화 여부입니다."
}

variable "rosa_data_security_group_ids" {
  type        = map(string)
  default     = {}
  nullable    = false
  description = "Foundation에서 인계한 DB와 Valkey 보안 그룹 번호입니다."

  validation {
    condition = !var.enable_rosa_data_connection_permissions || (
      toset(keys(var.rosa_data_security_group_ids)) == toset(["rds", "redis"])
      && length(toset(values(var.rosa_data_security_group_ids))) == 2
      && alltrue([
        for id in values(var.rosa_data_security_group_ids) :
        can(regex("^sg-([0-9a-f]{8}|[0-9a-f]{17})$", id))
      ])
    )
    error_message = "서로 다른 실제 DB·Valkey 보안 그룹 두 개를 공급해야 합니다."
  }
}

data "aws_iam_policy_document" "tf_rosa_data_connection" {
  count = var.enable_rosa_data_connection_permissions ? 1 : 0

  statement {
    sid = "ReadSeoulSecurityGroups"
    actions = [
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSecurityGroupRules",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
  }

  statement {
    sid = "ManageSuppliedDataIngress"
    actions = [
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupIngress",
    ]
    resources = [
      for id in values(var.rosa_data_security_group_ids) :
      "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/${id}"
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
  }

  # 태그를 붙여 생성하는 새 규칙의 번호는 미리 확정할 수 없습니다.
  # 대상 보안 그룹 자체는 위 문장에서 실제 두 개로 제한합니다.
  statement {
    sid       = "AuthorizeTaggedIngressRule"
    actions   = ["ec2:AuthorizeSecurityGroupIngress"]
    resources = ["arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group-rule/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["seokpan"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Phase"
      values   = ["2"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Component"
      values   = ["rosa"]
    }
  }

  statement {
    sid       = "TagIngressRuleOnCreate"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group-rule/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["AuthorizeSecurityGroupIngress"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["seokpan"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Phase"
      values   = ["2"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Component"
      values   = ["rosa"]
    }
  }
}

resource "aws_iam_role_policy" "tf_rosa_data_connection" {
  count = var.enable_rosa_data_connection_permissions ? 1 : 0

  name   = "seokpan-tf-rosa-data-connect"
  role   = aws_iam_role.tf["rosa"].id
  policy = data.aws_iam_policy_document.tf_rosa_data_connection[0].json

  lifecycle {
    precondition {
      condition = length(jsonencode(jsondecode(
        data.aws_iam_policy_document.tf_rosa_data_connection[0].json
      ))) <= 10240
      error_message = "새 정책의 크기 제한을 초과했습니다. 역할별 전체 합계는 별도 검사합니다."
    }
  }
}
