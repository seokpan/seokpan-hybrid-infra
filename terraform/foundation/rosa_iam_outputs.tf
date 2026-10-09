# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA 공통 역할과 Operator 정책 ARN의 제한 출력 (#47)

# 실제 ARN은 AWS 자원 참조로 얻으며 임의 문자열을 만들지 않습니다.
# 비활성 상태에서는 빈 대응표를 반환합니다.
# 출력 선언 작성이나 Plan의 예상값을 실제 인계 완료로 기록하지 않습니다.

output "rosa_account_role_arns" {
  description = "공통 역할 네 개의 ARN입니다. 실제 적용 후 소비 입력과 대조합니다."
  value = {
    for key, role in aws_iam_role.rosa_account :
    key => role.arn
  }

  depends_on = [
    aws_iam_role_policy_attachment.rosa_account
  ]
}

output "rosa_operator_policy_arns" {
  description = "태훈 님의 소비 정책 이름과 실제 Operator 정책 ARN의 대응표입니다."
  value = {
    for name, policy in aws_iam_policy.rosa_operator :
    name => policy.arn
  }
}
