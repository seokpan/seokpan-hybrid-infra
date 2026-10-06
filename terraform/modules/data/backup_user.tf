# Backup 전용 IAM User (03 §3-C.13, §3-D.9.2)
#
# - Console 로그인 없음, Access Key는 Terraform 밖에서 발급한다.
#   (aws_iam_access_key로 만들면 Secret Key가 State에 저장되기 때문)
# - 전용 Data VM의 예약 Job과 온프렘 Recovery Storage 다운로드에 사용
# - 삭제 권한 없음: Job이 잘못 동작해도 기존 백업을 지우지 못하게 함
#   (정리는 Lifecycle 규칙이 담당)

resource "aws_iam_user" "backup" {
  count = var.create_backup_user ? 1 : 0

  name = "${var.name_prefix}-backup"

  # bootstrap 소유 Boundary (PR #34). 이 값이 정확히 일치할 때만 foundation Role의 CreateUser가 허용된다.
  # foundation Role에 iam:GetPolicy가 없어 data 소스 대신 ARN 문자열로 조합한다.
  permissions_boundary = "arn:aws:iam::${data.aws_caller_identity.data.account_id}:policy/seokpan-fnd-backup-boundary"

  tags = {
    Component = "data"
  }
}

data "aws_iam_policy_document" "backup_user" {
  # 업로드는 일반 사본 경로에만
  statement {
    sid       = "PutHourly"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.backup.arn}/hourly/*"]
  }

  # 다운로드는 일반·보호 사본 모두 (온프렘 Recovery Storage 동기화)
  statement {
    sid       = "GetBackups"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.backup.arn}/hourly/*", "${aws_s3_bucket.backup.arn}/protected/*"]
  }

  # 목록 조회는 두 경로 안에서만
  statement {
    sid       = "ListBackupPrefixes"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.backup.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["hourly/*", "protected/*"]
    }
  }
}

resource "aws_iam_user_policy" "backup" {
  count = var.create_backup_user ? 1 : 0

  name   = "${var.name_prefix}-backup-s3"
  user   = aws_iam_user.backup[0].name
  policy = data.aws_iam_policy_document.backup_user.json
}
