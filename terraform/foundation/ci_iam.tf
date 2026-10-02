# 작성자: 최유준
# 작성 날짜: 2026/10/02
# ---------------------------------------------------------------------------
# foundation CI IAM User: seokpan-fnd-ci  (#18, 03 §3-C.11)
# - 콘솔 로그인 없음 (aws_iam_user_login_profile을 만들지 않음)
# - Access Key는 Terraform으로 만들지 않음. 별도 발급 후 SOPS+age Automation-CI 번들로 보관 (State에 비밀값 미저장)
# - Permissions Boundary(seokpan-fnd-ci-boundary)는 bootstrap이 소유. 지정하지 않으면 CreateUser가 AccessDenied (PR #21)
# - 정책 Action은 Boundary(ECR push/pull) 안으로 제한. 유효 권한은 정책과 Boundary의 교집합
# - BatchDeleteImage는 부여하지 않음 (이미지 정리는 ECR Lifecycle이 담당)
# ---------------------------------------------------------------------------

data "aws_caller_identity" "ci" {}

resource "aws_iam_user" "ci" {
  name                 = "seokpan-fnd-ci"
  permissions_boundary = "arn:aws:iam::${data.aws_caller_identity.ci.account_id}:policy/seokpan-fnd-ci-boundary"
  force_destroy        = false

  tags = {
    Component = "ci"
  }
}

data "aws_iam_policy_document" "ci_ecr" {
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
    resources = [for r in aws_ecr_repository.this : r.arn]
  }
}

resource "aws_iam_user_policy" "ci_ecr" {
  name   = "seokpan-fnd-ci-ecr"
  user   = aws_iam_user.ci.name
  policy = data.aws_iam_policy_document.ci_ecr.json
}

output "ci_user_name" {
  description = "CI IAM User 이름 (Access Key는 Terraform 밖에서 발급)"
  value       = aws_iam_user.ci.name
}
