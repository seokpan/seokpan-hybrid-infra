# Amazon RDS for MariaDB Multi-AZ (03 §3-D.10.3, infra #17)

resource "aws_db_subnet_group" "this" {
  name        = "${var.name_prefix}-data"
  description = "Data Private Subnet 3 AZ"
  subnet_ids  = var.data_subnet_ids

  tags = {
    Component = "data"
  }
}

# 파라미터 그룹 — 1차 MariaDB와 동작을 맞추는 값만 바꾼다
resource "aws_db_parameter_group" "mariadb" {
  name        = "${var.name_prefix}-mariadb118"
  family      = "mariadb11.8"
  description = "Seokpan MariaDB 11.8 (1st phase compatible)"

  # 1차 서버가 KST로 동작하고 날짜 컬럼이 모두 DATETIME이다.
  # RDS 기본 UTC로 두면 이관 후 NOW() 등으로 새로 쓰는 값만 9시간 어긋난다.
  parameter {
    name  = "time_zone"
    value = "Asia/Seoul"
  }

  # 1차와 같은 SQL Mode — 같은 SQL이 같은 결과(오류 여부 포함)를 내도록
  parameter {
    name  = "sql_mode"
    value = "STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION"
  }

  # 11.8은 기본값이 이미 1이지만, 설계 요구(TLS 필수)를 코드에 드러내기 위해 명시
  parameter {
    name  = "require_secure_transport"
    value = "1"
  }

  # 1차 stone_game 기본 문자셋과 동일
  parameter {
    name  = "character_set_server"
    value = "utf8mb4"
  }

  parameter {
    name  = "collation_server"
    value = "utf8mb4_unicode_ci"
  }

  tags = {
    Component = "data"
  }

  lifecycle {
    # 파라미터 그룹 교체(이름 변경 등) 시 새 그룹을 먼저 만든 뒤 옛 그룹 삭제
    create_before_destroy = true
  }
}

resource "aws_db_instance" "mariadb" {
  identifier     = "${var.name_prefix}-mariadb"
  engine         = "mariadb"
  engine_version = var.rds_engine_version
  instance_class = var.rds_instance_class

  # Multi-AZ DB instance (Primary 1 + Standby 1). Standby는 읽기용이 아님
  multi_az = true

  storage_type          = "gp3"
  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = 0 # 자동 확장 끔 (늘린 용량은 줄일 수 없음, 03 §3-D.10.3)
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  port                   = 3306
  parameter_group_name   = aws_db_parameter_group.mariadb.name
  ca_cert_identifier     = "rds-ca-rsa2048-g1"

  # 마스터 비밀번호는 RDS가 생성해 Secrets Manager에 보관한다.
  # → Terraform 코드·변수·State 어디에도 비밀번호가 들어가지 않고,
  #   apply 담당자도 비밀번호를 알 필요가 없다.
  username                    = var.rds_master_username
  manage_master_user_password = true

  # db_name을 비워 두면 빈 DB를 만들지 않는다.
  # stone_game은 논리 덤프 가져오기 때 원본과 같은 정의로 생성된다.

  # RDS 자동 백업·PITR (Cloud 내부 복구용, 온프렘 Portable Backup과 별개)
  backup_retention_period = var.rds_backup_retention_days
  backup_window           = "17:00-17:30"         # UTC = KST 02:00
  maintenance_window      = "sun:18:00-sun:19:00" # UTC = KST 월 03:00
  copy_tags_to_snapshot   = true

  # 검증한 버전에서 바뀌지 않도록 자동 마이너 업그레이드 끔 (03 §3-D.10.2)
  auto_minor_version_upgrade = false
  apply_immediately          = var.rds_apply_immediately

  deletion_protection       = var.rds_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name_prefix}-mariadb-final"

  # 비용 절감: 고급 모니터링 끔
  performance_insights_enabled = false
  monitoring_interval          = 0

  tags = {
    Name      = "${var.name_prefix}-mariadb"
    Component = "data"
  }
}
