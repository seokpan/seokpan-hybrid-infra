variable "aws_region" {
  description = "AWS Region for foundation resources"
  type        = string
  default     = "ap-northeast-2"
}

# 작성자: 김상희
# 작성 날짜: 2026/10/06
# 작성 내용: 온프렘 작업 Host 공통 입력 추가 (Infra #16 합의, Data Root 전환 PR)
# ---------------------------------------------------------------------------
# Data 전용이 아니라 foundation 공통 입력이다. 아래가 모두 같은 목록을 참조한다.
#   - Data: RDS SG 규칙 rds_from_onprem (data_security_groups.tf)
#   - Network/Hybrid(#16 후속): AWS Data Route · VPN 경로
# 목록이 비어 있으면 관련 SG 규칙 · Route를 만들지 않는다.
# 실제 주소(Data VM 192.168.52.50/32, #19 확정)는 기본값에 넣지 않고 Git 밖 실행용 입력 파일로만 공급한다.
# ---------------------------------------------------------------------------
variable "onprem_job_host_cidrs" {
  description = "On-Prem job host addresses (/32 only) allowed to reach AWS Data over the VPN. Empty list creates no rule or route."
  type        = list(string)
  default     = []

  validation {
    # cidrnetmask()는 IPv4 CIDR만 받는다 → IPv6(예: 2001:db8::/32)와 잘못된 IPv4는 여기서 걸러짐
    # 소비 Resource가 cidr_ipv4이므로 Plan 전에 IPv4 Host /32만 통과시킨다 (PR #37 리뷰)
    condition     = alltrue([for c in var.onprem_job_host_cidrs : can(cidrnetmask(c)) && endswith(c, "/32")])
    error_message = "onprem_job_host_cidrs에는 IPv4 Host /32만 넣습니다 (예: 192.168.52.50/32)."
  }
}
