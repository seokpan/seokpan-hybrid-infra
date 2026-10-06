# ---------------------------------------------------------------------------
# Network에서 받는 값 (foundation Root가 Network 리소스를 연결해 넘겨줌)
# ---------------------------------------------------------------------------

variable "name_prefix" {
  description = "리소스 이름 접두사 — foundation 자원은 seokpan-fnd- (infra #23, bootstrap 권한 ARN 패턴과 일치해야 함)"
  type        = string
  default     = "seokpan-fnd"
}

variable "vpc_id" {
  description = "Project VPC ID (192.168.64.0/20)"
  type        = string
}

variable "data_subnet_ids" {
  description = "Data Private Subnet ID 3개 (AZ-A/B/C 순서, 192.168.70~72.0/24)"
  type        = list(string)

  validation {
    condition     = length(var.data_subnet_ids) == 3
    error_message = "Data Private Subnet은 3개(AZ-A/B/C)여야 합니다."
  }
}

variable "onprem_job_host_cidrs" {
  description = <<-EOT
    RDS 3306 접근을 허용할 온프렘 Data VM 주소 목록(/32만).
    Data VM 주소가 확정되기 전에는 빈 목록으로 두어 규칙을 만들지 않는다 (03 §3-B.9.2).
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.onprem_job_host_cidrs : endswith(c, "/32")])
    error_message = "온프렘 허용 주소는 /32 단위로만 넣습니다."
  }
}

# ---------------------------------------------------------------------------
# RDS for MariaDB
# ---------------------------------------------------------------------------

variable "rds_engine_version" {
  description = "1차와 같은 MariaDB 11.8.9 (infra #17). 서울 리전 생성 가능 여부는 plan에서 확인"
  type        = string
  default     = "11.8.9"
}

variable "rds_instance_class" {
  description = "03 §3-D.10.3 초기 후보"
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
# ElastiCache for Redis OSS
# ---------------------------------------------------------------------------

variable "redis_engine_version" {
  description = "Redis OSS 엔진 버전. App Driver 호환 확인 후 고정 (03 §3-D.10.2)"
  type        = string
  default     = "7.1"
}

variable "redis_parameter_family" {
  description = "redis_engine_version과 맞는 파라미터 그룹 계열"
  type        = string
  default     = "redis7"
}

variable "redis_node_type" {
  description = "03 §3-D.10.4 초기 후보"
  type        = string
  default     = "cache.t4g.small"
}

variable "redis_auth_token" {
  description = <<-EOT
    Redis AUTH Token (16~128자, 출력 가능한 ASCII 중 @ " / 공백 제외).
    write-only 인자로만 전달되어 State·Plan에 남지 않는다.
    값은 SOPS 원본에서 현재 셸의 TF_VAR_redis_auth_token으로만 공급한다.
  EOT
  type        = string
  sensitive   = true
  ephemeral   = true
  default     = null
}

variable "redis_auth_token_version" {
  description = "Token을 바꿀 때만 1씩 올린다. 값이 바뀔 때만 새 Token이 전송됨"
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# Backup S3
# ---------------------------------------------------------------------------

variable "backup_hourly_retention_days" {
  description = "일반(1시간 주기) 사본 보관 일수 (03 §3-D.9.6)"
  type        = number
  default     = 7
}

variable "create_backup_user" {
  description = "Backup 전용 IAM User를 이 모듈에서 만들지 여부 (Access Key는 Terraform 밖에서 발급)"
  type        = bool
  default     = true
}
