# 작성자: 김상희
# 작성 날짜: 2026/10/06
# ---------------------------------------------------------------------------
# foundation Data 전용 변수 · 공통 값 (infra #19, #23)
# - 공통 변수(aws_region, onprem_job_host_cidrs)는 variables.tf에서 관리하며 여기서 다시 선언하지 않는다
# - Data 전용 변수는 rds_ · redis_ · backup_ 접두사를 쓴다
# - Network 값은 변수로 받지 않고 같은 Root의 aws_vpc.main · aws_subnet.data를 직접 참조한다
# ---------------------------------------------------------------------------

locals {
  # AWS 리소스 이름 접두사. bootstrap의 Data 권한 ARN 패턴(seokpan-fnd-*)과 맞아야 하므로
  # 입력으로 바꿀 수 없게 local로 고정한다 (infra #23)
  data_name_prefix = "seokpan-fnd"

  # Data Private Subnet 3개. values()는 키 이름 순서(az_a → az_b → az_c)로 돌려준다
  data_subnet_ids = [for subnet in values(aws_subnet.data) : subnet.id]
}

# ---------------------------------------------------------------------------
# RDS for MariaDB
# ---------------------------------------------------------------------------

variable "rds_engine_version" {
  description = "1차와 같은 MariaDB 11.8.9 (infra #17). 서울 db.t4g.small · Multi-AZ · gp3 생성 가능 조회 완료"
  type        = string
  default     = "11.8.9"
}

variable "rds_instance_class" {
  description = "03 §3-D.10.3 초기 후보. 연결 수 예산은 Data 계약 v2.2 2.6절"
  type        = string
  default     = "db.t4g.small"
}

variable "rds_allocated_storage" {
  description = "gp3 최소 20GiB. 실제 데이터 0.36MB라 확장 불필요, 자동 확장은 끔"
  type        = number
  default     = 20
}

variable "rds_master_username" {
  description = "DB 초기 관리 계정 이름 (비밀번호는 RDS가 Secrets Manager에 생성·보관)"
  type        = string
  default     = "seokpan_admin"
}

variable "rds_backup_retention_days" {
  description = "RDS 자동 백업·PITR 보관 일수 (온프렘 Portable Backup과 별개)"
  type        = number
  default     = 7
}

variable "rds_deletion_protection" {
  description = "삭제 보호. foundation 전체 Destroy는 별도 승인 조건이므로 기본 true"
  type        = bool
  default     = true
}

variable "rds_apply_immediately" {
  description = "설정 변경을 즉시 적용할지 여부 (false면 유지보수 시간에 적용)"
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# ElastiCache for Valkey (Redis 프로토콜 Runtime State, Data 계약 v2.2 2.5절)
# 리소스 · 변수 이름의 redis는 계층 이름으로 유지한다 (엔진만 Valkey)
# ---------------------------------------------------------------------------

variable "redis_engine_version" {
  description = "ElastiCache Valkey 엔진 버전 (팀 결정 10-06, App 시험 기준 Redis 7.2.4와 같은 계열)"
  type        = string
  default     = "7.2"
}

variable "redis_parameter_family" {
  description = "redis_engine_version과 맞는 파라미터 그룹 계열"
  type        = string
  default     = "valkey7"
}

variable "redis_node_type" {
  description = "03 §3-D.10.4 초기 후보"
  type        = string
  default     = "cache.t4g.small"
}

variable "redis_auth_token" {
  description = <<-EOT
    Redis(Valkey) AUTH Token — 16~128자, 영문 · 숫자와 특수문자 ! & # $ ^ < > - 만 (ElastiCache AUTH 허용 문자).
    write-only 인자로만 전달되어 State·Plan에 남지 않는다.
    값은 SOPS 원본에서 현재 셸의 TF_VAR_redis_auth_token으로만 공급한다 (절차: README "Redis Token 공급 절차").
    기본값이 없으므로 값 없이 Plan하면 멈춘다 → AUTH 없는 Redis가 만들어지는 것을 막음.
  EOT
  type        = string
  sensitive   = true
  ephemeral   = true
  nullable    = false

  validation {
    # AWS가 허용하는 문자만 통과시키는 허용 목록 방식 → 잘못된 Token이 Apply 도중이 아니라 Plan 전에 걸러짐 (PR #37 리뷰)
    condition     = can(regex("^[A-Za-z0-9!&#$^<>-]{16,128}$", var.redis_auth_token))
    error_message = "redis_auth_token은 16~128자, 영문 · 숫자와 ! & # $ ^ < > - 만 쓸 수 있습니다."
  }
}

variable "redis_auth_token_version" {
  description = "Token을 바꿀 때만 1씩 올린다. 값이 바뀔 때만 새 Token이 전송됨"
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# Backup S3 · Backup IAM User
# ---------------------------------------------------------------------------

variable "backup_hourly_retention_days" {
  description = "일반 사본(hourly/ 경로, 현재 15분 주기) 보관 일수 (03 §3-D.9.6, 3-I.14). 경로 이름은 재검토 중"
  type        = number
  default     = 7
}

variable "backup_user_enabled" {
  description = "Backup 전용 IAM User(seokpan-fnd-backup) 생성 여부 (Access Key는 Terraform 밖에서 발급)"
  type        = bool
  default     = true
}
