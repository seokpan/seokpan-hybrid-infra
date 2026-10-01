terraform {
  required_version = "1.16.4"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.67.0"
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

    # 삭제 마커 정리 (해당 키의 이전 버전이 모두 만료된 경우에만 동작)
    expiration {
      expired_object_delete_marker = true
    }

    # 중단된 멀티파트 업로드 정리
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# State 버킷 정책 (03 §3-F.15.1)
# 1) HTTP(비암호화) 요청 거부
# 2) TF 실행 Role은 자기 Root의 state key 접두사 밖 객체 접근 불가
# 3) TF 실행 Role은 State 객체 삭제·객체 Version 삭제·버킷 삭제 불가 (Lock 삭제만 허용)
# 4) foundation / rosa Role은 자기 접두사 밖 List 불가, 그 외 버킷 단위 작업(설정 조회·변경) 불가
# 사람 IAM User(AdministratorAccess)의 직접 접근은 막지 않음 (03 §3-C.3 협업 모델)
resource "aws_s3_bucket_policy" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }],
      [for k, r in aws_iam_role.tf : {
        Sid         = "DenyObjectsOutsideOwnPrefix${title(k)}"
        Effect      = "Deny"
        Principal   = { AWS = r.arn }
        Action      = "s3:*"
        NotResource = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/${local.tf_roles[k].state_prefix}/*"]
      }],
      [{
        Sid       = "DenyVersionAndBucketDeleteByTfRoles"
        Effect    = "Deny"
        Principal = { AWS = [for r in aws_iam_role.tf : r.arn] }
        Action    = ["s3:DeleteObjectVersion", "s3:DeleteBucket"]
        Resource  = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
      }],
      [{
        Sid       = "DenyStateObjectDeleteByTfRoles"
        Effect    = "Deny"
        Principal = { AWS = [for r in aws_iam_role.tf : r.arn] }
        Action    = "s3:DeleteObject"
        Resource  = "${aws_s3_bucket.tfstate.arn}/*.tfstate"
      }],
      [for k, v in local.tf_workload_roles : {
        Sid       = "DenyListOutsideOwnPrefix${title(k)}"
        Effect    = "Deny"
        Principal = { AWS = aws_iam_role.tf[k].arn }
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.tfstate.arn
        Condition = { StringNotLike = { "s3:prefix" = ["${v.state_prefix}/*"] } }
      }],
      [{
        Sid       = "DenyBucketLevelActionsByWorkloadRoles"
        Effect    = "Deny"
        Principal = { AWS = [for k in keys(local.tf_workload_roles) : aws_iam_role.tf[k].arn] }
        NotAction = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.tfstate.arn
      }]
    )
  })

  depends_on = [aws_s3_bucket_public_access_block.tfstate]
}

output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.id
}
