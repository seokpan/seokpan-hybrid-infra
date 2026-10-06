# Terraform 실행 Role (03 §3-C.4, §3-F.4.2, §3-F.15)
# - Root(bootstrap / foundation / rosa)별 실행 Role을 bootstrap이 소유
# - 사람 IAM User + MFA 조건으로만 AssumeRole 허용
# - 현재 단계: foundation Role은 자기 State/Lock 및 아래 tf_foundation_registry_ci(Registry/CI),
#   tf_foundation_data(Data 계층) 블록의 지정 자원 관리 권한을 가짐
#   각 Root 구현 PR에서 필요한 Action/Resource만 이 파일에 추가
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
    resources = [
      "arn:aws:iam::${local.account_id}:policy/seokpan-fnd-ci-boundary",
      # 2026/10/06 김상희: Backup IAM User Boundary (infra #19)
      "arn:aws:iam::${local.account_id}:policy/seokpan-fnd-backup-boundary",
    ]
  }
}

resource "aws_iam_role_policy" "tf_bootstrap" {
  name   = "seokpan-tf-bootstrap"
  role   = aws_iam_role.tf["bootstrap"].id
  policy = data.aws_iam_policy_document.tf_bootstrap.json
}

# ---------------------------------------------------------------------------
# foundation / rosa Role의 backend 접근 정책(tf_backend): 자기 Root의 State/Lock 접근 범위 (03 §3-F.15.1)
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
#     iam:CreateAccessKey, iam:CreateLoginProfile, iam:AttachUserPolicy, iam:PassRole, s3:PutBucketPolicy, s3:DeleteBucketPolicy
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

# 작성자: 김상희
# 작성 날짜: 2026/10/06
# ---------------------------------------------------------------------------
# foundation Role 추가 권한: Data 계층 (03 §3-C.13, §3-D.9.2, §3-D.10, infra #19)
# - 대상: RDS for MariaDB · ElastiCache Redis OSS · Data SG(RDS/Redis) · Backup S3 버킷 · Backup IAM User
# - 이름 접두사 seokpan-fnd-* 자원만 관리 (infra #23)
# - 의도적으로 제외한 Action (Terraform이 데이터·자격증명에 접근하지 않도록 함):
#     백업 객체 읽기/쓰기/삭제(s3:GetObject, s3:PutObject, s3:DeleteObject),
#     secretsmanager:GetSecretValue, iam:CreateAccessKey, iam:CreateLoginProfile,
#     iam:AttachUserPolicy, iam:PassRole, rds:RestoreDBInstance*, elasticache:TestFailover
#   (Backup Access Key는 Terraform 밖에서 발급해 SOPS+age로 보관, 복원·장애 시험은 담당자 작업)
# - KMS: RDS 관리형 마스터 Secret용 kms:DescribeKey만 alias/aws/secretsmanager 키로 한정해 허용
#   (그 외 kms:* 는 추가하지 않음, 실제 AccessDenied가 나는 Action만 보강)
# - 실제 plan/apply 중 AccessDenied가 나는 Action만 이 블록에 추가 (기본값을 넓게 잡지 않음)
# ---------------------------------------------------------------------------
locals {
  # Backup 버킷은 foundation이 만들지만 이름 규칙이 고정이므로 ARN을 미리 계산
  backup_bucket_arn = "arn:aws:s3:::seokpan-fnd-backup-${local.account_id}"
}

data "aws_iam_policy_document" "tf_foundation_data" {
  # ① 조회: 리소스 단위 제한을 지원하지 않거나, Network가 만든 VPC·Subnet을 읽음
  statement {
    sid = "DescribeDataResources"
    actions = [
      "rds:DescribeDBInstances",
      "rds:DescribeDBSubnetGroups",
      "rds:DescribeDBParameterGroups",
      "rds:DescribeDBParameters",
      "rds:DescribeDBEngineVersions",
      "rds:DescribeOrderableDBInstanceOptions",
      "elasticache:DescribeReplicationGroups",
      "elasticache:DescribeCacheClusters",
      "elasticache:DescribeCacheSubnetGroups",
      "elasticache:DescribeCacheParameterGroups",
      "elasticache:DescribeCacheParameters",
      "elasticache:DescribeCacheEngineVersions",
      "ec2:DescribeVpcs",
      "ec2:DescribeSubnets",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSecurityGroupRules",
      "ec2:DescribeNetworkInterfaces",
    ]
    resources = ["*"]
  }

  # ② RDS: Subnet Group · Parameter Group · DB Instance · 최종 스냅샷
  statement {
    sid = "ManageSeokpanRds"
    actions = [
      "rds:CreateDBSubnetGroup",
      "rds:ModifyDBSubnetGroup",
      "rds:DeleteDBSubnetGroup",
      "rds:CreateDBParameterGroup",
      "rds:ModifyDBParameterGroup",
      "rds:ResetDBParameterGroup",
      "rds:DeleteDBParameterGroup",
      "rds:CreateDBInstance",
      "rds:ModifyDBInstance",
      "rds:DeleteDBInstance",
      "rds:AddTagsToResource",
      "rds:RemoveTagsFromResource",
      "rds:ListTagsForResource",
    ]
    resources = [
      "arn:aws:rds:ap-northeast-2:${local.account_id}:db:seokpan-fnd-*",
      "arn:aws:rds:ap-northeast-2:${local.account_id}:subgrp:seokpan-fnd-*",
      "arn:aws:rds:ap-northeast-2:${local.account_id}:pg:seokpan-fnd-*",
      "arn:aws:rds:ap-northeast-2:${local.account_id}:snapshot:seokpan-fnd-*",
    ]
  }

  # CreateDBInstance/ModifyDBInstance는 기본 Option Group(default:mariadb-11-8)도 함께 평가
  statement {
    sid       = "UseDefaultRdsOptionGroup"
    actions   = ["rds:CreateDBInstance", "rds:ModifyDBInstance"]
    resources = ["arn:aws:rds:ap-northeast-2:${local.account_id}:og:default:*"]
  }

  # ③ manage_master_user_password: RDS가 호출자 권한으로 마스터 Secret을 생성
  #    기본 aws/secretsmanager 키를 쓰더라도 호출자에게 kms:DescribeKey가 필요 (RDS 공식 요구사항)
  #    GetSecretValue는 주지 않음 → foundation Role은 마스터 비밀번호를 읽을 수 없음
  statement {
    sid       = "CreateRdsManagedMasterSecret"
    actions   = ["secretsmanager:CreateSecret", "secretsmanager:TagResource"]
    resources = ["arn:aws:secretsmanager:ap-northeast-2:${local.account_id}:secret:rds!db-*"]
  }

  statement {
    sid       = "DescribeDefaultSecretsManagerKey"
    actions   = ["kms:DescribeKey"]
    resources = ["arn:aws:kms:ap-northeast-2:${local.account_id}:key/*"]

    condition {
      test     = "ForAnyValue:StringEquals"
      variable = "kms:ResourceAliases"
      values   = ["alias/aws/secretsmanager"]
    }
  }

  # ElastiCache: Subnet Group · Parameter Group · Replication Group(멤버 cluster 포함)
  statement {
    sid = "ManageSeokpanElastiCache"
    actions = [
      "elasticache:CreateCacheSubnetGroup",
      "elasticache:ModifyCacheSubnetGroup",
      "elasticache:DeleteCacheSubnetGroup",
      "elasticache:CreateCacheParameterGroup",
      "elasticache:ModifyCacheParameterGroup",
      "elasticache:ResetCacheParameterGroup",
      "elasticache:DeleteCacheParameterGroup",
      "elasticache:CreateReplicationGroup",
      "elasticache:ModifyReplicationGroup",
      "elasticache:DeleteReplicationGroup",
      "elasticache:AddTagsToResource",
      "elasticache:RemoveTagsFromResource",
      "elasticache:ListTagsForResource",
    ]
    resources = [
      "arn:aws:elasticache:ap-northeast-2:${local.account_id}:replicationgroup:seokpan-fnd-*",
      "arn:aws:elasticache:ap-northeast-2:${local.account_id}:cluster:seokpan-fnd-*",
      "arn:aws:elasticache:ap-northeast-2:${local.account_id}:subnetgroup:seokpan-fnd-*",
      "arn:aws:elasticache:ap-northeast-2:${local.account_id}:parametergroup:seokpan-fnd-*",
    ]
  }

  # ④ Data SG: Component=data 태그로 범위 제한 (자원별 tags가 default_tags의 Component를 덮어씀)
  #    Network 권한 PR이 foundation SG 전체를 다루게 되면 이 4개 statement는 제거
  statement {
    sid       = "CreateSgInAnyVpc"
    actions   = ["ec2:CreateSecurityGroup"]
    resources = ["arn:aws:ec2:ap-northeast-2:${local.account_id}:vpc/*"]
  }

  statement {
    sid       = "CreateDataSecurityGroups"
    actions   = ["ec2:CreateSecurityGroup"]
    resources = ["arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Component"
      values   = ["data"]
    }
  }

  statement {
    sid       = "TagDataSecurityGroupsOnCreate"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["CreateSecurityGroup"]
    }
  }

  statement {
    sid = "ManageDataSecurityGroups"
    actions = [
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress", # 생성 직후 AWS 기본 egress 제거
      "ec2:ModifySecurityGroupRules",
      "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
      "ec2:CreateTags",
      "ec2:DeleteTags",
    ]
    resources = ["arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Component"
      values   = ["data"]
    }
  }

  # 규칙 자원(sgr-) 자체: SG 쪽 조건(Component=data)이 함께 평가되므로 다른 SG에는 적용 불가
  statement {
    sid = "ManageDataSecurityGroupRules"
    actions = [
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:ModifySecurityGroupRules",
      "ec2:CreateTags",
      "ec2:DeleteTags",
    ]
    resources = ["arn:aws:ec2:ap-northeast-2:${local.account_id}:security-group-rule/*"]
  }

  # ⑤ Backup S3 버킷: 버킷 설정만 관리, 객체 Action 없음
  #    ListBucket은 Terraform의 HeadBucket 확인용 (객체 이름 목록은 보이나 내용은 읽을 수 없음)
  #    버킷 정책 변경 불가, 객체 접근은 explicit Deny
  statement {
    sid = "ManageBackupBucket"
    actions = [
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
      "s3:PutLifecycleConfiguration",
      "s3:PutBucketTagging",
    ]
    resources = [local.backup_bucket_arn]
  }

  # 정책 우회 방어: 같은 계정 Bucket Policy가 Allow를 주더라도 explicit Deny가 이김
  statement {
    sid       = "DenyBackupObjectAccess"
    effect    = "Deny"
    actions   = ["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*"]
    resources = ["${local.backup_bucket_arn}/*"]
  }

  # ⑥ Backup IAM User: seokpan-fnd-backup 한 개만 (CI User 블록과 같은 구조)
  statement {
    sid = "ManageSeokpanBackupIamUser"
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
    resources = ["arn:aws:iam::${local.account_id}:user/seokpan-fnd-backup"]
  }

  # Boundary(seokpan-fnd-backup-boundary)를 지정한 경우에만 User 생성/Boundary 설정 허용
  statement {
    sid       = "CreateBackupUserOnlyWithBoundary"
    actions   = ["iam:CreateUser", "iam:PutUserPermissionsBoundary"]
    resources = ["arn:aws:iam::${local.account_id}:user/seokpan-fnd-backup"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.backup_boundary.arn]
    }
  }

  # Boundary 제거 금지
  statement {
    sid       = "DenyRemoveBackupUserBoundary"
    effect    = "Deny"
    actions   = ["iam:DeleteUserPermissionsBoundary"]
    resources = ["arn:aws:iam::${local.account_id}:user/seokpan-fnd-backup"]
  }

  # ⑦ RDS·ElastiCache 첫 생성 시 서비스 연결 Role
  #    2026/10/06 확인: 계정에 AWSServiceRoleForRDS · AWSServiceRoleForElastiCache 모두 없음
  #    → 첫 DB Instance / Replication Group 생성 때 AWS가 자동 생성하며, 이때 호출자에게 이 권한이 필요
  #    SLR은 이후 destroy해도 남으므로, 두 Role이 생긴 뒤에는 이 statement를 제거해도 됨
  statement {
    sid     = "CreateDataServiceLinkedRoles"
    actions = ["iam:CreateServiceLinkedRole"]
    resources = [
      "arn:aws:iam::${local.account_id}:role/aws-service-role/rds.amazonaws.com/AWSServiceRoleForRDS",
      "arn:aws:iam::${local.account_id}:role/aws-service-role/elasticache.amazonaws.com/AWSServiceRoleForElastiCache",
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["rds.amazonaws.com", "elasticache.amazonaws.com"]
    }
  }
}

# 작성자: 김상희
# 작성 날짜: 2026/10/06
# ---------------------------------------------------------------------------
# Backup IAM User(seokpan-fnd-backup) Permissions Boundary (03 §3-D.9.2)
# - Backup User의 identity-based 권한이 가질 수 있는 최대 범위를 백업 버킷 업로드·다운로드·목록으로 고정
# - 삭제·버킷 관리 없음 (정리는 Lifecycle 규칙이 담당). Prefix 세부 제한은 foundation의 User inline policy
# - bootstrap이 소유: foundation Role은 이 Policy를 수정/삭제할 수 없음
# - apply 순서: bootstrap Role의 ManageCiBoundaryPolicy 권한(tf_bootstrap) 적용 후 생성
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "backup_boundary" {
  statement {
    sid       = "BackupObjectPutGet"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${local.backup_bucket_arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["true"]
    }
  }

  statement {
    sid       = "BackupBucketList"
    actions   = ["s3:ListBucket"]
    resources = [local.backup_bucket_arn]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["true"]
    }
  }

  # Bucket Policy 등 resource-based Allow가 있어도 삭제는 막음 (explicit Deny가 우선)
  statement {
    sid       = "DenyBackupObjectDelete"
    effect    = "Deny"
    actions   = ["s3:DeleteObject*"]
    resources = ["${local.backup_bucket_arn}/*"]
  }
}

resource "aws_iam_policy" "backup_boundary" {
  name        = "seokpan-fnd-backup-boundary"
  description = "Permissions boundary for backup IAM user seokpan-fnd-backup (backup bucket put/get/list only)"
  policy      = data.aws_iam_policy_document.backup_boundary.json

  tags = {
    Component = "backup"
  }

  depends_on = [aws_iam_role_policy.tf_bootstrap]
}

resource "aws_iam_role_policy" "tf_foundation_data" {
  name   = "seokpan-tf-foundation-data"
  role   = aws_iam_role.tf["foundation"].id
  policy = data.aws_iam_policy_document.tf_foundation_data.json
}

output "tf_exec_role_arns" {
  description = "Root별 Terraform 실행 Role ARN"
  value       = { for k, r in aws_iam_role.tf : k => r.arn }
}
