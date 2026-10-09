# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA 공통 IAM 생성 여부·이름·경로 입력 준비 (#47)

variable "enable_rosa_account_iam" {
  description = "ROSA 공통 IAM 생성 여부입니다. 후속 자원 선언에서 사용하며 기본값은 비활성입니다."
  type        = bool
  default     = false
  nullable    = false
}

variable "rosa_account_role_prefix" {
  description = "승인한 ROSA 공통 역할 이름 접두사입니다. 확정 전에는 null로 유지합니다."
  type        = string
  default     = "seokpan-fnd-rosa"

  validation {
    condition = (
      var.rosa_account_role_prefix == null
      ? true
      : can(regex("^[A-Za-z0-9_-]{1,32}$", var.rosa_account_role_prefix))
    )
    error_message = "역할 접두사는 영문·숫자·밑줄·하이픈으로 구성한 1~32자여야 합니다."
  }
}

variable "rosa_iam_path" {
  description = "ROSA 공통 IAM 경로입니다. 현재 생성 활성 시 /만 지원하며 다른 경로는 정책 묶음 검사에서 차단합니다."
  type        = string
  default     = "/"
  nullable    = false

  validation {
    condition = (
      length(var.rosa_iam_path) <= 512 &&
      can(regex("^(/|/[!-~]+/)$", var.rosa_iam_path))
    )
    error_message = "IAM 경로는 / 또는 /로 시작하고 끝나는 공백 없는 ASCII 경로이며 최대 512자여야 합니다."
  }
}
