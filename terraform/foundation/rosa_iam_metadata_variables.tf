# 작성자: 이유빈
# 작성일: 2026-10-08
# 작성내용: ROSA 공통 IAM의 OpenShift 버전 계열 입력 (#47)

# 실제 지원·소비 버전을 확인한 후 Git 밖 입력으로 공급합니다.
# Provider 버전이나 Terraform 버전을 변경하는 변수가 아닙니다.
# 공통 IAM 생성 시 필수 입력 여부는 자원 선언에서 검사합니다.

variable "rosa_iam_openshift_minor_version" {
  description = "공식 ROSA IAM 태그에 사용할 OpenShift 버전 계열입니다. 실제 지원·소비 버전과 대조한 major.minor 값을 공급합니다."
  type        = string
  default     = null

  validation {
    condition = (
      var.rosa_iam_openshift_minor_version == null
      ? true
      : can(regex(
        "^[0-9]+\\.[0-9]+$",
        var.rosa_iam_openshift_minor_version
      ))
    )
    error_message = "OpenShift 버전 계열은 숫자.숫자 형식이어야 합니다. 실제 지원 여부는 별도로 확인합니다."
  }
}
