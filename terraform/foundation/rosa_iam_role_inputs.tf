# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA 공통 역할의 선택적 권한 경계 입력 (#47)

# 현재 생성 활성 시 null만 지원하며, ARN 지정은 정책 묶음 검사에서 차단합니다.
# 실제 계정에 권한 경계가 필요하면 bootstrap 권한과 함께 별도 보완·리뷰합니다.
# 기본값 null이 해당 계정에서 권한 경계가 불필요함을 증명하지 않습니다.
# 클러스터 전용 Operator 역할의 권한 경계와는 별도입니다.

variable "rosa_account_permissions_boundary" {
  description = "공통 역할의 권한 경계 입력입니다. 현재 생성 활성 시 null만 지원합니다. 권한 경계 사용에는 bootstrap 권한의 별도 보완이 필요합니다."
  type        = string
  default     = null

  validation {
    condition = (
      var.rosa_account_permissions_boundary == null
      ? true
      : can(regex(
        "^arn:aws:iam::[0-9]{12}:policy/.+$",
        var.rosa_account_permissions_boundary
      ))
    )
    error_message = "권한 경계는 AWS IAM 정책 ARN 형식이어야 합니다."
  }
}
