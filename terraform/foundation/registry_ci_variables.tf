# 작성자: 최유준
# 작성 날짜: 2026/10/02
# ---------------------------------------------------------------------------
# foundation Registry/CI 전용 변수 (#18)
# - 공통 변수는 이유빈 담당 공통 파일에서 관리하며, 이 파일은 Registry/CI 전용만 둔다
# - 아래 두 값은 비용 집계 후 협의 전의 임시 기본값
# ---------------------------------------------------------------------------

variable "registry_keep_image_count" {
  description = "ECR Repository별 보관할 최근 이미지 개수 (GitOps가 참조 중인 git-* 이미지가 삭제되지 않는 값이어야 함)"
  type        = number
  default     = 50 # seokpan-hybrid-app #2 "최신 50개 유지" 기준

  validation {
    condition     = var.registry_keep_image_count >= 1
    error_message = "registry_keep_image_count는 1 이상이어야 합니다."
  }
}

variable "registry_untagged_expire_days" {
  description = "untagged 이미지를 만료시킬 경과 일수"
  type        = number
  default     = 7 # TODO: 비용 집계 후 협의 (임시값)

  validation {
    condition     = var.registry_untagged_expire_days >= 1
    error_message = "registry_untagged_expire_days는 1 이상이어야 합니다."
  }
}
