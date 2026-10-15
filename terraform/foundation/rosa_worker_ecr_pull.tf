# 작성자: 최유준
# 작성 날짜: 2026/10/15
# ---------------------------------------------------------------------------
# ROSA Worker Role ECR Pull 전용 정책 (#74, 03 §3-F.16)
# - Worker 노드 Role로 FE/BE 두 저장소만 Pull. Push/Delete는 부여하지 않음
# - 정책 이름은 bootstrap의 rosa_execution_worker_ecr_pull_policy_arn과 같아야 함
# - Worker Role과 공식 정책은 rosa_iam_roles.tf 소유. 이 파일은 정책과 연결만 추가
# - enable_rosa_account_iam=true일 때만 생성
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "rosa_worker_ecr_pull" {
  statement {
    sid       = "EcrAuthToken"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPullSeokpanRepositories"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [for r in aws_ecr_repository.this : r.arn]
  }
}

resource "aws_iam_policy" "rosa_worker_ecr_pull" {
  count = var.enable_rosa_account_iam ? 1 : 0

  name   = "seokpan-fnd-rosa-Worker-ECR-Pull"
  path   = var.rosa_iam_path
  policy = data.aws_iam_policy_document.rosa_worker_ecr_pull.json

  tags = {
    Component = "registry"
  }
}

resource "aws_iam_role_policy_attachment" "rosa_worker_ecr_pull" {
  count = var.enable_rosa_account_iam ? 1 : 0

  role       = aws_iam_role.rosa_account["worker"].name
  policy_arn = aws_iam_policy.rosa_worker_ecr_pull[0].arn
}
