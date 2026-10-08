# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: 검증한 ROSA 공식 정책 묶음의 보호 입력 정의 (#47)

# 실제 보호 경로와 채택한 manifest 해시는 Git 밖 입력으로 공급합니다.
# 공식 정책 묶음을 새로 조회하거나 인증 토큰을 공급하는 변수가 아닙니다.
# 생성 활성화 시 필수 입력 검사는 후속 자원 선언에서 연결합니다.

variable "rosa_policy_bundle_directory" {
  description = "검증한 ROSA 공식 정책 묶음이 있는 Git 밖 폴더의 절대경로입니다."
  type        = string
  default     = null

  validation {
    condition = (
      var.rosa_policy_bundle_directory == null
      ? true
      : startswith(var.rosa_policy_bundle_directory, "/")
    )
    error_message = "Controller에서 사용하는 정책 묶음 폴더는 절대경로여야 합니다."
  }
}

variable "rosa_policy_bundle_manifest_sha256" {
  description = "검토해 채택한 정책 묶음 manifest.json의 SHA-256입니다."
  type        = string
  default     = null

  validation {
    condition = (
      var.rosa_policy_bundle_manifest_sha256 == null
      ? true
      : can(regex("^[0-9a-f]{64}$", var.rosa_policy_bundle_manifest_sha256))
    )
    error_message = "manifest SHA-256은 소문자 16진수 64자리여야 합니다."
  }
}
