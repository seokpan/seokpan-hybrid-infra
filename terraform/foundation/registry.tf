# 작성자: 최유준
# 작성 날짜: 2026/10/02
# ---------------------------------------------------------------------------
# foundation Registry: ECR Repository 2개 (backend / frontend)  (#18, 03 §3-C.11)
# - 이름: seokpan-fnd-backend, seokpan-fnd-frontend (#16 네이밍 합의)
# - 태그 Immutable, 암호화 AES256(KMS 비의존)
# - ECR은 Persistent 자원이므로 force_delete = false
# - 수명 정책: 저장소별 최신 N개 유지 규칙 하나만 둔다 (#18 합의)
#   untagged 만료 규칙은 image index 하위 manifest/attestation 영향을
#   Lifecycle Preview로 확인하기 전까지 넣지 않는다 (확인 후 별도 PR에서 검토)
#   scan-*/git-* 접두사별 규칙도 사용하지 않음 (이미지 단위 만료로 git-* 태그까지 삭제될 위험)
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
