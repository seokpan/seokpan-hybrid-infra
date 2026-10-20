# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: Foundation의 VPN SSM Role·Profile 관리 권한 초안 (#16)

data "aws_iam_policy_document" "tf_foundation_hybrid" {

  # 전용 파일 전달 버킷의 설정만 관리합니다.
  # Ansible 운영자의 파일 전달 권한은 별도로 준비합니다.
  statement {
    sid = "ManageVpnTransferBucket"

    actions = [
      "s3:PutLifecycleConfiguration",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:ListBucket",
      "s3:GetBucket*",
      "s3:GetLifecycleConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:GetAccelerateConfiguration",
      "s3:PutBucketVersioning",
      "s3:PutEncryptionConfiguration",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketOwnershipControls",
      "s3:PutBucketTagging",
      "s3:DeleteBucketOwnershipControls",
    ]

    resources = [
      "arn:aws:s3:::seokpan-fnd-vpn-transfer-${local.account_id}",
    ]
  }

  # 지정한 서버 Role 한 개만 생성·조회·수정·삭제합니다.
  statement {
    sid = "ManageVpnSsmRole"

    actions = [
      "iam:CreateRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "iam:GetRolePolicy",
      "iam:ListInstanceProfilesForRole",
    ]

    resources = [
      "arn:aws:iam::${local.account_id}:role/seokpan-fnd-vpn-ssm",
    ]
  }

  # 위 Role에는 기존 AWS 관리형 SSM 정책만 연결할 수 있습니다.
  statement {
    sid     = "AttachVpnSsmCoreOnly"
    actions = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]

    resources = [
      "arn:aws:iam::${local.account_id}:role/seokpan-fnd-vpn-ssm",
    ]

    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values = [
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
      ]
    }
  }

  # 지정한 Profile 한 개만 만들고 관리합니다.
  statement {
    sid = "ManageVpnSsmProfile"

    actions = [
      "iam:CreateInstanceProfile",
      "iam:GetInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:UntagInstanceProfile",
      "iam:ListInstanceProfileTags",
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
    ]

    resources = [
      "arn:aws:iam::${local.account_id}:instance-profile/seokpan-fnd-vpn-ssm",
    ]
  }

  # 지정한 Role을 EC2 서비스에 전달할 수 있도록 합니다.
  statement {
    sid     = "PassVpnSsmRoleToEc2"
    actions = ["iam:PassRole"]

    resources = [
      "arn:aws:iam::${local.account_id}:role/seokpan-fnd-vpn-ssm",
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }
  # 기존 조회 권한에 없는 EC2·디스크 상태 조회를 추가합니다.
  # 쓰기 권한이 아니며 서울 리전에서만 허용합니다.
  statement {
    sid = "ReadVpnComputeState"

    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceAttribute",
      "ec2:DescribeInstanceStatus",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeImages",
      "ec2:DescribeVolumes",
      "ec2:DescribeVolumeAttribute",
      "ec2:DescribeInstanceCreditSpecifications",
    ]

    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = ["ap-northeast-2"]
    }
  }

}

# Terraform 실행용 Hybrid 정책입니다.
# 서버에 연결하는 AmazonSSMManagedInstanceCore와는 별개입니다.
resource "aws_iam_policy" "tf_foundation_hybrid" {
  name        = "seokpan-tf-foundation-hybrid"
  description = "Foundation VPN resource management"
  policy      = data.aws_iam_policy_document.tf_foundation_hybrid.json

  tags = {
    Component = "tf-exec-role"
  }

  depends_on = [aws_iam_role_policy.tf_bootstrap]

  lifecycle {
    precondition {
      condition = length(jsonencode(jsondecode(
        data.aws_iam_policy_document.tf_foundation_hybrid.json
      ))) <= 6144
      error_message = "Hybrid managed policy exceeds the 6144-character limit."
    }
  }
}

# Hybrid 정책을 Foundation 실행 Role에 연결합니다.
resource "aws_iam_role_policy_attachment" "tf_foundation_hybrid" {
  role       = aws_iam_role.tf["foundation"].name
  policy_arn = aws_iam_policy.tf_foundation_hybrid.arn
}
