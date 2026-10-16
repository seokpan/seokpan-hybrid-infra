# 작성자: 이유빈
# 작성일: 2026-10-07
# 작성내용: Ansible SSM 파일 전달용 전용 S3 버킷 초안 (#16)
#
# State·백업 버킷과 분리합니다.
# 운영 접속 권한·비밀값 공급은 후속 작업입니다.
# 이 파일에 인증정보나 WireGuard 개인키를 넣지 않습니다.

data "aws_caller_identity" "vpn_transfer" {
  count = var.enable_vpn_resources ? 1 : 0
}

resource "aws_s3_bucket" "vpn_transfer" {
  count = var.enable_vpn_resources ? 1 : 0

  bucket = "seokpan-fnd-vpn-transfer-${data.aws_caller_identity.vpn_transfer[0].account_id}"

  # 객체가 남아 있으면 함께 삭제하지 않습니다.
  force_destroy = false

  # 공통 태그는 기존 Provider의 default_tags를 사용합니다.
  tags = {
    Component = "hybrid"
  }
}

# 파일 삭제 후 이전 버전이 계속 남는 것을 방지합니다.
# 기존에 생성된 이전 버전까지 삭제하는 설정은 아닙니다.
resource "aws_s3_bucket_versioning" "vpn_transfer" {
  count = var.enable_vpn_resources ? 1 : 0

  bucket = aws_s3_bucket.vpn_transfer[0].id

  versioning_configuration {
    status = "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "vpn_transfer" {
  count = var.enable_vpn_resources ? 1 : 0

  bucket = aws_s3_bucket.vpn_transfer[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "vpn_transfer" {
  count = var.enable_vpn_resources ? 1 : 0

  bucket                  = aws_s3_bucket.vpn_transfer[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "vpn_transfer" {
  count = var.enable_vpn_resources ? 1 : 0

  bucket = aws_s3_bucket.vpn_transfer[0].id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

output "vpn_ansible_transfer_bucket_name" {
  description = "Ansible SSM 파일 전달용 버킷 이름입니다. 생성 비활성 상태에서는 null입니다."
  value       = try(aws_s3_bucket.vpn_transfer[0].id, null)
}

# 운영 사용자와 역할의 비암호화 HTTP 요청을 거부합니다.
# 이 정책은 접근 권한을 새로 부여하지 않습니다.
resource "aws_s3_bucket_policy" "vpn_transfer" {
  count = var.enable_vpn_resources ? 1 : 0

  bucket = aws_s3_bucket.vpn_transfer[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.vpn_transfer[0].arn,
          "${aws_s3_bucket.vpn_transfer[0].arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport"       = "false"
            "aws:PrincipalIsAWSService" = "false"
          }
        }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.vpn_transfer]
}

# 정상 종료 시 플러그인이 삭제하지 못한 임시 파일을 정리합니다.
# 만료 시점과 실제 삭제 시점은 다를 수 있습니다.
# 백업·복구 원본·장기 보관 로그는 이 버킷에 저장하지 않습니다.
resource "aws_s3_bucket_lifecycle_configuration" "vpn_transfer" {
  count = var.enable_vpn_resources ? 1 : 0

  bucket = aws_s3_bucket.vpn_transfer[0].id

  rule {
    id     = "expire-transfer-files"
    status = "Enabled"

    filter {}

    expiration {
      days = 1
    }
  }

  rule {
    id     = "cleanup-transfer-remnants"
    status = "Enabled"

    filter {}

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.vpn_transfer]
}
