variable "network_az_ids" {
  description = "Three AZ IDs for the approved network layout; verify service support before apply."
  type        = map(string)

  default = {
    az_a = "apne2-az1"
    az_b = "apne2-az2"
    az_c = "apne2-az3"
  }

  validation {
    condition = (
      toset(keys(var.network_az_ids)) == toset(["az_a", "az_b", "az_c"]) &&
      length(toset(values(var.network_az_ids))) == 3
    )
    error_message = "Use az_a, az_b and az_c with three distinct AZ IDs."
  }
}

# 작성자: 이유빈
# 작성일: 2026-10-06
# 작성 내용: Cost Window에 맞춘 NAT Gateway 생성·삭제 제어 변수 추가 (#23)
# ---------------------------------------------------------------------------
variable "enable_nat_gateways" {
  description = "Create NAT Gateways, NAT EIPs, and ROSA default Internet routes only during an approved cost window."
  type        = bool
  default     = false
}
