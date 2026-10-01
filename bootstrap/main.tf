terraform {
  required_version = ">= 1.16.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-2"
  default_tags {
    tags = {
      Project   = "seokpan"
      Phase     = "2"
      ManagedBy = "terraform"
      Component = "tfstate"
    }
  }
}

data "aws_caller_identity" "current" {}

# 버킷 이름은 전 세계 고유해야 하므로 계정 ID를 붙임
resource "aws_s3_bucket" "tfstate" {
  bucket = "seokpan-tfstate-${data.aws_caller_identity.current.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

# state 복구용 버전 관리
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

# 저장 시 암호화
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 퍼블릭 접근 전면 차단
resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACL 비활성화, 버킷 소유자가 모든 객체 소유
resource "aws_s3_bucket_ownership_controls" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# state 이전 버전 관리 및 잔여 객체 정리
resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}

    # 최신 5개 이전 버전은 보존, 그보다 오래된 버전만 90일 후 삭제
    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 5
    }

    # use_lockfile의 .tflock 삭제로 남는 삭제 마커 정리
    expiration {
      expired_object_delete_marker = true
    }

    # 중단된 멀티파트 업로드 정리
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# HTTP(비암호화) 요청 거부
resource "aws_s3_bucket_policy" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.tfstate]
}

output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.id
}
