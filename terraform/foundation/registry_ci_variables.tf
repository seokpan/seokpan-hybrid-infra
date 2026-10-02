# 작성자: 최유준
# 작성 날짜: 2026/10/02
# ---------------------------------------------------------------------------
# foundation Registry/CI 전용 변수 (#18)
# - 공통 변수는 이유빈 담당 공통 파일에서 관리하며, 이 파일은 Registry/CI 전용만 둔다
# - registry_keep_image_count는 임시 입력값이다. 생성 후 Lifecycle Preview로
#   GitOps 사용 Digest, rollback 후보, image index/하위 manifest를 확인해 최종 N을 확정한다
# ---------------------------------------------------------------------------

variable "registry_keep_image_count" {
  description = "ECR Repository별 보관할 최근 이미지 개수. 임시 후보값이며 Lifecycle Preview 후 최종 확정 (GitOps가 참조 중인 git-* 이미지가 삭제되지 않는 값이어야 함)"
  type        = number
  default     = 50 # 임시 후보. 최종 N은 Lifecycle Preview 후 확정

  validation {
    condition     = var.registry_keep_image_count >= 1
    error_message = "registry_keep_image_count는 1 이상이어야 합니다."
  }
}
