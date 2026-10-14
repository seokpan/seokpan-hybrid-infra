# 작성자: 이유빈
# 작성일: 2026-10-14
# 작성 내용: ROSA 실행 역할의 지정 Operator 역할과 OIDC 관리 권한
# 기존 조회 정책·Foundation 역할·Trust·ROSA Source는 수정하지 않습니다.
# 기본 비활성이며 PR 리뷰·병합·전체 Plan 검토·승인 후 적용합니다.
# OIDC 생성 요청 태그와 기존 자원 태그를 구분합니다.
# 실제 AWS 호출 성공은 별도로 확인합니다.

variable "enable_rosa_cluster_iam_permissions" {
  type        = bool
  default     = false
  nullable    = false
  description = "ROSA 설치용 IAM 실행 권한의 생성 여부입니다."
}

variable "rosa_cluster_operator_policy_arns" {
  type        = map(string)
  default     = {}
  nullable    = false
  description = "Foundation에서 인계한 Operator 정책 6개입니다."

  validation {
    condition = !var.enable_rosa_cluster_iam_permissions || (
      toset(keys(var.rosa_cluster_operator_policy_arns)) == toset(["seokpan-fnd-rosa-openshift-cloud-credential-operator-cloud-crede", "seokpan-fnd-rosa-openshift-cloud-network-config-controller-cloud", "seokpan-fnd-rosa-openshift-cluster-csi-drivers-ebs-cloud-credent", "seokpan-fnd-rosa-openshift-image-registry-installer-cloud-creden", "seokpan-fnd-rosa-openshift-ingress-operator-cloud-credentials", "seokpan-fnd-rosa-openshift-machine-api-aws-cloud-credentials"])
      && alltrue([
        for name, arn in var.rosa_cluster_operator_policy_arns :
        arn == "arn:aws:iam::${local.account_id}:policy/${name}"
      ])
    )
    error_message = "같은 계정과 경로 /의 지정 Operator 정책 6개를 공급해야 합니다."
  }
}

data "aws_iam_policy_document" "tf_rosa_cluster_iam" {
  count = var.enable_rosa_cluster_iam_permissions ? 1 : 0

  source_policy_documents = [
    <<-POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ManageSixClusterOperatorRoles",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:GetRole",
        "iam:TagRole",
        "iam:ListRolePolicies",
        "iam:GetRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:ListInstanceProfilesForRole",
        "iam:DeleteRole"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-cloud-credential-operator-cloud-crede",
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-cloud-network-config-controller-cloud",
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-cluster-csi-drivers-ebs-cloud-credent",
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-image-registry-installer-cloud-creden",
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-ingress-operator-cloud-credentials",
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-machine-api-aws-cloud-credentials"
      ]
    },
    {
      "Sid": "AttachOperatorPolicy1",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-cloud-credential-operator-cloud-crede"
      ],
      "Condition": {
        "ArnEquals": {
          "iam:PolicyARN": "${var.rosa_cluster_operator_policy_arns["seokpan-fnd-rosa-openshift-cloud-credential-operator-cloud-crede"]}"
        }
      }
    },
    {
      "Sid": "AttachOperatorPolicy2",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-cloud-network-config-controller-cloud"
      ],
      "Condition": {
        "ArnEquals": {
          "iam:PolicyARN": "${var.rosa_cluster_operator_policy_arns["seokpan-fnd-rosa-openshift-cloud-network-config-controller-cloud"]}"
        }
      }
    },
    {
      "Sid": "AttachOperatorPolicy3",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-cluster-csi-drivers-ebs-cloud-credent"
      ],
      "Condition": {
        "ArnEquals": {
          "iam:PolicyARN": "${var.rosa_cluster_operator_policy_arns["seokpan-fnd-rosa-openshift-cluster-csi-drivers-ebs-cloud-credent"]}"
        }
      }
    },
    {
      "Sid": "AttachOperatorPolicy4",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-image-registry-installer-cloud-creden"
      ],
      "Condition": {
        "ArnEquals": {
          "iam:PolicyARN": "${var.rosa_cluster_operator_policy_arns["seokpan-fnd-rosa-openshift-image-registry-installer-cloud-creden"]}"
        }
      }
    },
    {
      "Sid": "AttachOperatorPolicy5",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-ingress-operator-cloud-credentials"
      ],
      "Condition": {
        "ArnEquals": {
          "iam:PolicyARN": "${var.rosa_cluster_operator_policy_arns["seokpan-fnd-rosa-openshift-ingress-operator-cloud-credentials"]}"
        }
      }
    },
    {
      "Sid": "AttachOperatorPolicy6",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:role/seokpan-cloud-op-openshift-machine-api-aws-cloud-credentials"
      ],
      "Condition": {
        "ArnEquals": {
          "iam:PolicyARN": "${var.rosa_cluster_operator_policy_arns["seokpan-fnd-rosa-openshift-machine-api-aws-cloud-credentials"]}"
        }
      }
    },
    {
      "Sid": "CreateTaggedRosaOidc",
      "Effect": "Allow",
      "Action": [
        "iam:CreateOpenIDConnectProvider"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:oidc-provider/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/Project": "seokpan",
          "aws:RequestTag/Phase": "2",
          "aws:RequestTag/Component": "rosa"
        }
      }
    },
    {
      "Sid": "ReadDeleteTaggedRosaOidc",
      "Effect": "Allow",
      "Action": [
        "iam:GetOpenIDConnectProvider",
        "iam:ListOpenIDConnectProviderTags",
        "iam:DeleteOpenIDConnectProvider"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:oidc-provider/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/Project": "seokpan",
          "aws:ResourceTag/Phase": "2",
          "aws:ResourceTag/Component": "rosa"
        }
      }
    },
    {
      "Sid": "TagOwnedRosaOidc",
      "Effect": "Allow",
      "Action": [
        "iam:TagOpenIDConnectProvider"
      ],
      "Resource": [
        "arn:aws:iam::${local.account_id}:oidc-provider/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/Project": "seokpan",
          "aws:ResourceTag/Phase": "2",
          "aws:ResourceTag/Component": "rosa"
        },
        "StringEqualsIfExists": {
          "aws:RequestTag/Project": "seokpan",
          "aws:RequestTag/Phase": "2",
          "aws:RequestTag/Component": "rosa"
        }
      }
    }
  ]
}
    POLICY
  ]
}

resource "aws_iam_role_policy" "tf_rosa_cluster_iam" {
  count = var.enable_rosa_cluster_iam_permissions ? 1 : 0

  name   = "seokpan-tf-rosa-cluster-iam"
  role   = aws_iam_role.tf["rosa"].id
  policy = data.aws_iam_policy_document.tf_rosa_cluster_iam[0].json

  lifecycle {
    precondition {
      condition = length(jsonencode(jsondecode(
        data.aws_iam_policy_document.tf_rosa_cluster_iam[0].json
      ))) <= 10240
      error_message = "새 정책이 크기 제한을 초과합니다. 역할별 전체 합계는 별도로 검사합니다."
    }
  }
}
