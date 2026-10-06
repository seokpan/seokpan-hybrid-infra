# 작성자: 김상희
# 작성 날짜: 2026/10/02 (foundation Root 전환 2026/10/06, infra #19)
# foundation Data 출력 (Data 계약 v2.2 5절)
# 비밀값은 출력하지 않는다. Data 서브넷 ID는 Network 출력 network_subnets에 있어 따로 내보내지 않는다.
# 아래 값은 03 §3-F.5의 "제한된 입력 파일"로 rosa·GitOps 환경 설정에 전달할 후보다.

output "rds_security_group_id" {
  description = "rosa State가 ROSA Worker SG → RDS 3306 규칙을 추가할 대상"
  value       = aws_security_group.rds.id
}

output "redis_security_group_id" {
  description = "rosa State가 ROSA Worker SG → Redis 6379 규칙을 추가할 대상"
  value       = aws_security_group.redis.id
}

output "rds_address" {
  description = "RDS Endpoint DNS (App·Data VM 접속용, IP 고정 금지)"
  value       = aws_db_instance.mariadb.address
}

output "rds_port" {
  value = aws_db_instance.mariadb.port
}

output "rds_master_secret_arn" {
  description = "마스터 비밀번호가 보관된 Secrets Manager ARN (값이 아니라 위치)"
  value       = aws_db_instance.mariadb.master_user_secret[0].secret_arn
}

output "redis_primary_endpoint" {
  description = "Redis Primary Endpoint DNS (읽기·쓰기 모두 이 주소)"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "redis_port" {
  value = aws_elasticache_replication_group.redis.port
}

output "backup_bucket_name" {
  value = aws_s3_bucket.backup.id
}

output "backup_user_name" {
  value = var.backup_user_enabled ? aws_iam_user.backup[0].name : null
}
