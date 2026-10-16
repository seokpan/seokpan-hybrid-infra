# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: AWS VPN EC2의 SSM Role·정책 연결·Instance Profile 선언 (#16)

# 서버의 SSM 권한입니다.
# 사람의 SSM 접속 권한과 Terraform 실행 권한은 별도로 관리합니다.
resource "aws_iam_role" "vpn_ssm" {
  count = var.enable_vpn_resources ? 1 : 0

  name        = "seokpan-fnd-vpn-ssm"
  description = "SSM agent role for the foundation VPN EC2"

  # EC2 서비스가 이 Role을 사용할 수 있도록 합니다.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Component = "hybrid"
  }
}

# 기존 AWS 관리형 SSM 정책을 Role에 연결합니다.
resource "aws_iam_role_policy_attachment" "vpn_ssm_core" {
  count = var.enable_vpn_resources ? 1 : 0

  role       = aws_iam_role.vpn_ssm[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# 이후 VPN EC2에 지정할 Profile입니다.
resource "aws_iam_instance_profile" "vpn_ssm" {
  count = var.enable_vpn_resources ? 1 : 0

  name = "seokpan-fnd-vpn-ssm"
  role = aws_iam_role.vpn_ssm[0].name

  tags = {
    Component = "hybrid"
  }
}
