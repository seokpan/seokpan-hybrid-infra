# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: VPN SG·전용 ENI·EIP 선언 (#16)

# 보안 그룹 본체와 규칙을 별도 리소스로 관리합니다.
resource "aws_security_group" "vpn" {
  count = var.enable_vpn_resources ? 1 : 0

  name        = "seokpan-fnd-vpn"
  description = "WireGuard VPN and management egress"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name      = "seokpan-fnd-vpn"
    Component = "hybrid"
  }
}

# 변동하는 학원 출구 IP를 고려한 단일 Public 허용 예외입니다.
# Public SSH는 개방하지 않습니다.
resource "aws_vpc_security_group_ingress_rule" "vpn_wireguard" {
  count = var.enable_vpn_resources ? 1 : 0

  security_group_id = aws_security_group.vpn[0].id
  description       = "WireGuard UDP endpoint"
  ip_protocol       = "udp"
  from_port         = 51820
  to_port           = 51820
  cidr_ipv4         = "0.0.0.0/0"

  tags = {
    Component = "hybrid"
  }
}

# 승인된 온프렘 작업 Host가 있을 때 Data 대역의 DB 포트를 허용합니다.
# 원본 Host 제한은 RDS SG와 Gateway 전달 방화벽에서도 검증합니다.
resource "aws_vpc_security_group_egress_rule" "vpn_to_data_db" {
  for_each = (
    var.enable_vpn_resources && length(var.onprem_job_host_cidrs) > 0
    ? aws_subnet.data
    : {}
  )

  security_group_id = aws_security_group.vpn[0].id
  description       = "DB forwarding to Data subnet"
  ip_protocol       = "tcp"
  from_port         = 3306
  to_port           = 3306
  cidr_ipv4         = each.value.cidr_block

  tags = {
    Component = "hybrid"
  }
}

# SSM 및 HTTPS 패키지 설치를 위한 초기 송신안입니다.
# HTTPS 전체 목적지 허용이며 SSM 서비스만 식별하는 규칙은 아닙니다.
resource "aws_vpc_security_group_egress_rule" "vpn_https" {
  count = var.enable_vpn_resources ? 1 : 0

  security_group_id = aws_security_group.vpn[0].id
  description       = "HTTPS for SSM and package installation"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"

  tags = {
    Component = "hybrid"
  }
}

# EC2와 별도 관리하여 같은 AZ에서 재사용하는 네트워크 카드입니다.
resource "aws_network_interface" "vpn" {
  count = var.enable_vpn_resources ? 1 : 0

  description       = "Dedicated foundation VPN interface"
  subnet_id         = aws_subnet.public["az_a"].id
  private_ips       = [var.vpn_private_ip]
  security_groups   = [aws_security_group.vpn[0].id]
  source_dest_check = false

  tags = {
    Name      = "seokpan-fnd-vpn-eni"
    Component = "hybrid"
    AZ        = var.network_az_ids["az_a"]
  }
}

# 고정 공인 주소입니다. ENI 연결은 다음 단계에서 추가합니다.
resource "aws_eip" "vpn" {
  count = var.enable_vpn_resources ? 1 : 0

  domain = "vpc"

  tags = {
    Name      = "seokpan-fnd-vpn-eip"
    Component = "hybrid"
  }
}
