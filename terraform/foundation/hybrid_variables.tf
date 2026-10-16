# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: VPN 유료 자원 생성 여부·AMI·사설 IP 입력 선언 (#16)

# 이후 VPN EC2·ENI·EIP·SG·반환 Route 코드가 참조합니다.
# Stop/Start 스위치가 아닙니다.
# 생성 이후 false로 바꾸면 해당 자원의 삭제 계획이 나올 수 있습니다.
variable "enable_vpn_resources" {
  description = "Create VPN infrastructure only after configuration and cost review."
  type        = bool
  default     = false
}

# 생성 전에 서울 리전의 승인된 AL2023 x86_64 AMI를 공급합니다.
# 생성 비활성 상태에서는 AMI 입력을 요구하지 않습니다.
variable "vpn_ami_id" {
  description = "Approved AL2023 x86_64 AMI ID; required when VPN creation is enabled."
  type        = string
  default     = null

  validation {
    condition = (
      var.vpn_ami_id == null ||
      can(regex("^ami-([0-9a-f]{8}|[0-9a-f]{17})$", var.vpn_ami_id))
    )
    error_message = "vpn_ami_id must be a valid AMI ID or null."
  }
}

# 상세설계의 후보 주소입니다. 실제 생성 전 중복을 확인합니다.
variable "vpn_private_ip" {
  description = "Candidate private IPv4 address for the dedicated VPN ENI."
  type        = string
  default     = "192.168.64.10"
  nullable    = false

  validation {
    condition     = can(cidrnetmask("${var.vpn_private_ip}/32"))
    error_message = "vpn_private_ip must be a valid IPv4 address."
  }
}
