# Data Security Group (03 §3-B.9.6, §3-B.9.7)
#
# - RDS SG와 Redis SG를 분리한다.
# - SG 본체는 foundation(이 모듈)이 소유하고, 규칙은 모두 "별도 Rule 리소스"로 만든다.
#   이유: ROSA Worker SG → Data SG 허용 규칙은 rosa State가 Cluster와 함께 만들고 지운다.
#         SG 본체에 inline ingress/egress 블록을 쓰면 foundation apply 때마다
#         rosa가 추가한 규칙을 "모르는 규칙"으로 보고 지워 버린다.
# - egress 블록을 선언하지 않으면 Terraform이 AWS 기본 전체 허용 egress를 제거한다.
#   RDS·Redis는 먼저 밖으로 나가는 연결이 없으므로 egress 규칙을 두지 않는다
#   (들어온 연결의 응답은 SG가 상태를 기억하므로 그대로 나간다).

resource "aws_security_group" "rds" {
  name        = "${var.name_prefix}-rds"
  description = "RDS MariaDB - ROSA Worker(rosa State), On-Prem Data VM /32 only"
  vpc_id      = var.vpc_id

  tags = {
    Name      = "${var.name_prefix}-rds"
    Component = "data"
  }
}

resource "aws_security_group" "redis" {
  name        = "${var.name_prefix}-redis"
  description = "ElastiCache Redis OSS - ROSA Worker(rosa State) only"
  vpc_id      = var.vpc_id

  tags = {
    Name      = "${var.name_prefix}-redis"
    Component = "data"
  }
}

# 온프렘 Data VM → RDS 3306 (이관·백업 작업, NET-06)
# 주소가 확정되기 전에는 목록이 비어 있어 규칙이 만들어지지 않는다.
resource "aws_vpc_security_group_ingress_rule" "rds_from_onprem" {
  for_each = toset(var.onprem_job_host_cidrs)

  security_group_id = aws_security_group.rds.id
  description       = "On-Prem Data VM via WireGuard"
  ip_protocol       = "tcp"
  from_port         = 3306
  to_port           = 3306
  cidr_ipv4         = each.value

  tags = {
    Component = "data"
  }
}

# Redis에는 foundation이 소유하는 규칙이 없다.
# 온프렘 → Redis 직접 접근은 기본 허용하지 않는다 (03 §3-D.9.7).
