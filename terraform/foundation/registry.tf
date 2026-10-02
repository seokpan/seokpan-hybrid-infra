# 작성자: 최유준
# 작성 날짜: 2026/10/02
# ---------------------------------------------------------------------------
# foundation Registry: ECR Repository 2개 (backend / frontend)  (#18, 03 §3-C.11)
# - 이름: seokpan-fnd-backend, seokpan-fnd-frontend (#16 네이밍 합의)
# - 태그 Immutable, 암호화 AES256(KMS 비의존)
# - ECR은 Persistent 자원이므로 force_delete = false
# - 수명 정책: untagged 만료 + 전체 최근 N개 보관 (scan-*/git-* 접두사별 규칙은 사용하지 않음)
#   scan-* 규칙을 두면, Promote 후 scan-* 태그가 남은 이미지가 이미지 단위로 만료되어
#   같은 이미지의 git-* 태그까지 삭제될 수 있어 단일 보관 규칙으로 구성
# - 변수는 registry_ci_variables.tf, 출력은 registry_ci_outputs.tf
# - 아직 미반영: ROSA Worker ECR Pull 권한(Role 이름/연결 방식 확인 후 별도 PR)
# ---------------------------------------------------------------------------

locals {
  registry_components = toset(["backend", "frontend"])
}

resource "aws_ecr_repository" "this" {
  for_each = local.registry_components

  name                 = "seokpan-fnd-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  # 취약점 스캔은 CI의 Trivy가 수행하므로 push 시 스캔은 사용하지 않음
  image_scanning_configuration {
    scan_on_push = false
  }

  tags = {
    Component = "registry"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "untagged 이미지 ${var.registry_untagged_expire_days}일 후 만료"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.registry_untagged_expire_days
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "최근 ${var.registry_keep_image_count}개 이미지만 보관"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.registry_keep_image_count
        }
        action = { type = "expire" }
      },
    ]
  })
}
