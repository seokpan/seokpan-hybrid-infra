# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: VPN EC2·EIP 연결·Data VM 반환 경로 선언 (#16)

resource "aws_instance" "vpn" {
  count = var.enable_vpn_resources ? 1 : 0

  ami                  = var.vpn_ami_id
  instance_type        = "t3.micro"
  iam_instance_profile = aws_iam_instance_profile.vpn_ssm[0].name

  # 첫 부팅 때 SSM 관리 프로그램을 준비합니다.
  user_data = file("${path.module}/templates/vpn-management-bootstrap.sh")

  # 별도로 관리하는 기본 ENI를 지정합니다.
  # 이 블록의 delete_on_termination은 읽기 전용 속성이므로 선언하지 않습니다.
  primary_network_interface {
    network_interface_id = aws_network_interface.vpn[0].id
  }

  root_block_device {
    encrypted             = true
    volume_type           = "gp3"
    volume_size           = 16
    delete_on_termination = true
  }

  # 생성 시 디스크에도 Hybrid 태그를 전달합니다.
  volume_tags = {
    Name      = "seokpan-fnd-vpn-root"
    Component = "hybrid"
  }

  # 추가 CPU 크레딧 과금이 없는 모드입니다.
  credit_specification {
    cpu_credits = "standard"
  }

  # 서버의 임시 Role 자격증명 조회에는 IMDSv2를 사용합니다.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  tags = {
    Name      = "seokpan-fnd-vpn"
    Component = "hybrid"
    AZ        = var.network_az_ids["az_a"]
    Lifecycle = "stoppable"
  }

  # SSM 정책 및 Public 인터넷 경로·HTTPS 송신 규칙을 먼저 준비합니다.
  depends_on = [
    aws_iam_role_policy_attachment.vpn_ssm_core,
    aws_route.public_internet,
    aws_route_table_association.public,
    aws_vpc_security_group_egress_rule.vpn_https,
  ]

  lifecycle {
    ignore_changes = [source_dest_check]

    precondition {
      condition     = var.vpn_ami_id != null
      error_message = "VPN 생성 시 승인된 AL2023 x86_64 AMI ID를 공급해야 합니다."
    }

    precondition {
      condition     = length(var.onprem_job_host_cidrs) > 0
      error_message = "VPN 생성 시 승인된 온프렘 작업 Host /32를 공급해야 합니다."
    }
  }
}

# EC2 준비 후 고정 공인 주소를 전용 ENI에 연결합니다.
resource "aws_eip_association" "vpn" {
  count = var.enable_vpn_resources ? 1 : 0

  allocation_id        = aws_eip.vpn[0].id
  network_interface_id = aws_network_interface.vpn[0].id
  allow_reassociation  = false

  depends_on = [aws_instance.vpn]
}

# RDS의 응답이 온프렘 Data VM으로 돌아가는 길입니다.
# RDS SG와 동일한 공통 입력을 사용합니다.
resource "aws_route" "data_to_onprem" {
  for_each = (
    var.enable_vpn_resources
    ? toset(var.onprem_job_host_cidrs)
    : toset([])
  )

  route_table_id         = aws_route_table.data.id
  destination_cidr_block = each.value
  network_interface_id   = aws_network_interface.vpn[0].id

  depends_on = [aws_instance.vpn]
}
