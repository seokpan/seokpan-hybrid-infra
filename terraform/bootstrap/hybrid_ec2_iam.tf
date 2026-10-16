# 작성자: 이유빈
# 작성일: 2026-10-13
# 작성 내용: 정책 크기 제한에 맞춰 선택 식별 이름을 주석으로 보존
# 작업·대상·조건·허용 또는 거부 내용은 변경하지 않음
# 문장 1의 기존 식별 이름: CreateVpnParts
# 문장 2의 기존 식별 이름: UseProjectVpc
# 문장 3의 기존 식별 이름: UseProjectSubnet
# 문장 4의 기존 식별 이름: UseVpnSg
# 문장 5의 기존 식별 이름: RunApprovedImage
# 문장 7의 기존 식별 이름: RunVpnInstance
# 문장 8의 기존 식별 이름: RunEncryptedVolume
# 문장 9의 기존 식별 이름: ManageVpnParts
# 문장 10의 기존 식별 이름: CreateVpnRules
# 문장 11의 기존 식별 이름: TagOnCreate
# 문장 12의 기존 식별 이름: UpdateVpnTags
# 문장 13의 기존 식별 이름: DeleteVpnTags

# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: Foundation의 VPN EC2·네트워크 관리 권한 초안 (#16)

# 기존 SSM 관리 권한과 분리한 EC2 관리 정책입니다.
data "aws_iam_policy_document" "tf_foundation_hybrid_ec2" {
  source_policy_documents = [
    <<-POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateNetworkInterface",
        "ec2:CreateSecurityGroup",
        "ec2:AllocateAddress"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:network-interface/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:elastic-ip/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/Project": "seokpan",
          "aws:RequestTag/Phase": "2",
          "aws:RequestTag/Component": "hybrid"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateSecurityGroup"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:vpc/*"
      ],
      "Condition": {
        "StringEquals": {
          "ec2:ResourceTag/Project": "seokpan",
          "ec2:ResourceTag/Phase": "2",
          "ec2:ResourceTag/Component": "network",
          "ec2:ResourceTag/Name": "seokpan-fnd-vpc"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateNetworkInterface",
        "ec2:RunInstances"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:subnet/*"
      ],
      "Condition": {
        "StringEquals": {
          "ec2:ResourceTag/Project": "seokpan",
          "ec2:ResourceTag/Phase": "2",
          "ec2:ResourceTag/Component": "network",
          "ec2:ResourceTag/Name": "seokpan-fnd-public-az_a",
          "ec2:AvailabilityZoneId": "apne2-az1"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateNetworkInterface",
        "ec2:RunInstances"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*"
      ],
      "Condition": {
        "StringEquals": {
          "ec2:ResourceTag/Project": "seokpan",
          "ec2:ResourceTag/Phase": "2",
          "ec2:ResourceTag/Component": "hybrid",
          "ec2:ResourceTag/Name": "seokpan-fnd-vpn"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2::image/ami-0870825cefcaafcc8"
      ]
    },
    {
      "Sid": "RunExistingEni",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:network-interface/*"
      ],
      "Condition": {
        "StringEquals": {
          "ec2:ResourceTag/Project": "seokpan",
          "ec2:ResourceTag/Phase": "2",
          "ec2:ResourceTag/Component": "hybrid",
          "ec2:ResourceTag/Name": "seokpan-fnd-vpn-eni"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:instance/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/Project": "seokpan",
          "aws:RequestTag/Phase": "2",
          "aws:RequestTag/Component": "hybrid",
          "aws:RequestTag/Name": "seokpan-fnd-vpn",
          "ec2:InstanceType": "t3.micro",
          "ec2:InstanceProfile": "arn:aws:iam::${local.account_id}:instance-profile/seokpan-fnd-vpn-ssm",
          "ec2:MetadataHttpTokens": "required",
          "ec2:AvailabilityZoneId": "apne2-az1"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:volume/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/Project": "seokpan",
          "aws:RequestTag/Phase": "2",
          "aws:RequestTag/Component": "hybrid",
          "ec2:VolumeType": "gp3"
        },
        "Bool": {
          "ec2:Encrypted": "true"
        },
        "NumericEquals": {
          "ec2:VolumeSize": "16"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:TerminateInstances",
        "ec2:ModifyInstanceAttribute",
        "ec2:ModifyInstanceMetadataOptions",
        "ec2:ModifyInstanceCreditSpecification",
        "ec2:DeleteNetworkInterface",
        "ec2:ModifyNetworkInterfaceAttribute",
        "ec2:ReleaseAddress",
        "ec2:AssociateAddress",
        "ec2:DisassociateAddress",
        "ec2:DeleteSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:AuthorizeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupEgress",
        "ec2:ModifySecurityGroupRules",
        "ec2:ModifyVolume"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:instance/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:network-interface/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:elastic-ip/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group-rule/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:volume/*"
      ],
      "Condition": {
        "StringEquals": {
          "ec2:ResourceTag/Project": "seokpan",
          "ec2:ResourceTag/Phase": "2",
          "ec2:ResourceTag/Component": "hybrid"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:AuthorizeSecurityGroupEgress"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group-rule/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/Project": "seokpan",
          "aws:RequestTag/Phase": "2",
          "aws:RequestTag/Component": "hybrid"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateTags"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:instance/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:network-interface/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:elastic-ip/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group-rule/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:volume/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/Project": "seokpan",
          "aws:RequestTag/Phase": "2",
          "aws:RequestTag/Component": "hybrid",
          "ec2:CreateAction": [
            "RunInstances",
            "CreateNetworkInterface",
            "CreateSecurityGroup",
            "AllocateAddress",
            "AuthorizeSecurityGroupIngress",
            "AuthorizeSecurityGroupEgress"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateTags"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:instance/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:network-interface/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:elastic-ip/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group-rule/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:volume/*"
      ],
      "Condition": {
        "StringEquals": {
          "ec2:ResourceTag/Project": "seokpan",
          "ec2:ResourceTag/Phase": "2",
          "ec2:ResourceTag/Component": "hybrid"
        },
        "StringEqualsIfExists": {
          "aws:RequestTag/Project": "seokpan",
          "aws:RequestTag/Phase": "2",
          "aws:RequestTag/Component": "hybrid"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DeleteTags"
      ],
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:instance/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:network-interface/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:elastic-ip/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group-rule/*",
        "arn:aws:ec2:ap-northeast-2:${local.account_id}:volume/*"
      ],
      "Condition": {
        "StringEquals": {
          "ec2:ResourceTag/Project": "seokpan",
          "ec2:ResourceTag/Phase": "2",
          "ec2:ResourceTag/Component": "hybrid"
        },
        "ForAllValues:StringNotEquals": {
          "aws:TagKeys": [
            "Project",
            "Phase",
            "Component"
          ]
        },
        "Null": {
          "aws:TagKeys": "false"
        }
      }
    }
  ]
}
    POLICY
  ]
}

resource "aws_iam_policy" "tf_foundation_hybrid_ec2" {
  name        = "seokpan-tf-foundation-hybrid-ec2"
  description = "Foundation VPN compute and network management"
  policy      = data.aws_iam_policy_document.tf_foundation_hybrid_ec2.json

  tags = {
    Component = "tf-exec-role"
  }

  depends_on = [aws_iam_role_policy.tf_bootstrap]

  lifecycle {
    precondition {
      condition = length(jsonencode(jsondecode(
        data.aws_iam_policy_document.tf_foundation_hybrid_ec2.json
      ))) <= 6144
      error_message = "VPN EC2 policy exceeds the 6144-character limit."
    }
  }
}

# 새 정책을 Foundation 실행 Role에 연결합니다.
resource "aws_iam_role_policy_attachment" "tf_foundation_hybrid_ec2" {
  role       = aws_iam_role.tf["foundation"].name
  policy_arn = aws_iam_policy.tf_foundation_hybrid_ec2.arn
}
