# 작성자: 최유준
# 작성 날짜: 2026/10/02
# ---------------------------------------------------------------------------
# foundation Registry/CI 전용 출력 (#18)
# - CI IAM User의 Access Key는 Terraform으로 만들지 않으므로 출력하지 않음
# ---------------------------------------------------------------------------

output "ecr_repository_urls" {
  description = "ECR Repository URL (component별)"
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "ci_user_name" {
  description = "CI IAM User 이름 (Access Key는 Terraform 밖에서 발급)"
  value       = aws_iam_user.ci.name
}
