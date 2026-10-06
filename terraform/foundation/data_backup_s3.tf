# 작성자: 김상희
# 작성 날짜: 2026/10/02 (foundation Root 전환 2026/10/06, infra #19)
# Backup용 S3 (03 §3-D.9.5~9.6)
#
# 경로 규칙
#   hourly/     주기 백업 일반 사본(10-05부터 15분 주기, 경로 이름은 재검토 중) → 7일 후 자동 삭제
#   protected/  마지막 복원 검증 사본·시험 전 사본 등 → 자동 삭제 없음 (사람이 정리)
#
# 이 버킷에는 실사용자 데이터가 들어간 백업이 저장된다 (age 암호화 후 업로드).
# 프로젝트 종료 시 직접 비우고 삭제한다 (infra #17 취급 조건).
#
# 버킷 정책은 두지 않는다 (PR #34).
#   - HTTPS 강제 · 삭제 금지: Backup User Boundary의 explicit Deny
#     (DenyBackupInsecureTransport, DenyBackupObjectDelete — bootstrap 소유)
#   - foundation Role은 PutBucketPolicy 권한이 없고, 백업 객체 접근은 explicit Deny

# 같은 foundation State의 다른 담당(CI는 .ci)과 이름이 겹치지 않도록 담당 이름 사용
data "aws_caller_identity" "data" {}

resource "aws_s3_bucket" "backup" {
  # 버킷 이름은 전 세계 고유해야 하므로 계정 ID를 붙임 (State 버킷과 같은 규칙)
  bucket = "${local.data_name_prefix}-backup-${data.aws_caller_identity.data.account_id}"

  # 객체가 남아 있으면 삭제 실패 → 백업이 실수로 함께 지워지는 것을 막음
  force_destroy = false

  # 공통 태그는 Provider default_tags, Data 영역은 Component만 덮어씀 (infra #23)
  tags = {
    Component = "data"
  }
}

# 같은 Key로 덮어써도 이전 사본이 남도록 버전 관리
resource "aws_s3_bucket_versioning" "backup" {
  bucket = aws_s3_bucket.backup.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "backup" {
  bucket                  = aws_s3_bucket.backup.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id

  # 일반 사본: 7일 후 만료, 덮어쓴 이전 버전은 1일 후 삭제
  rule {
    id     = "expire-hourly"
    status = "Enabled"
    filter {
      prefix = "hourly/"
    }
    expiration {
      days = var.backup_hourly_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  # 보호 사본: 현재 버전은 자동 삭제하지 않음, 덮어쓴 이전 버전만 30일 후 정리
  rule {
    id     = "protected-noncurrent-only"
    status = "Enabled"
    filter {
      prefix = "protected/"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  # 버킷 전체: 중단된 멀티파트 업로드·삭제 마커 정리
  rule {
    id     = "cleanup"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
    expiration {
      expired_object_delete_marker = true
    }
  }

  depends_on = [aws_s3_bucket_versioning.backup]
}

