# Amazon ElastiCache for Redis OSS (03 §3-D.9.7, §3-D.10.4)
# node-based, cluster mode disabled, Primary 1 + Replica 1, Multi-AZ, TLS + AUTH

resource "aws_elasticache_subnet_group" "this" {
  name        = "${var.name_prefix}-data"
  description = "Data Private Subnet 3 AZ"
  subnet_ids  = var.data_subnet_ids

  tags = {
    Component = "data"
  }
}

resource "aws_elasticache_parameter_group" "redis" {
  name        = "${var.name_prefix}-redis"
  family      = var.redis_parameter_family
  description = "Seokpan Redis OSS - noeviction"

  # 메모리가 차도 Room/Game Key를 임의로 지우지 않고 쓰기를 실패시킨다.
  # → 게임 상태 일부만 사라지는 상황 대신, App이 오류를 명시적으로 처리하게 함
  parameter {
    name  = "maxmemory-policy"
    value = "noeviction"
  }

  tags = {
    Component = "data"
  }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "Seokpan runtime state (session/room/game)"

  engine               = "redis"
  engine_version       = var.redis_engine_version
  node_type            = var.redis_node_type
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.redis.name

  # Primary 1 + Replica 1, 장애 시 자동 승격, 서로 다른 AZ
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]

  # TLS 필수 + AUTH Token
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  # write-only: 값은 AWS로 전송만 되고 State·Plan에 저장되지 않는다.
  # 버전 숫자가 바뀔 때만 새 Token을 보낸다.
  auth_token_wo         = var.redis_auth_token
  auth_token_wo_version = var.redis_auth_token_version

  # Runtime State는 보존·승계 대상이 아니므로 스냅샷을 만들지 않는다 (비용 절감)
  snapshot_retention_limit = 0

  auto_minor_version_upgrade = false
  apply_immediately          = true

  tags = {
    Name      = "${var.name_prefix}-redis"
    Component = "data"
  }
}
