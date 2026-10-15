provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "seokpan"
      Phase     = "2"
      ManagedBy = "terraform"
      Component = "foundation"
    }
  }
}

# ROSA가 동적으로 관리하는 클러스터 태그만 별도 소유권으로 둡니다.
# 이 설정을 명시한 VPC/설치 Subnet/공통 Role에만 적용합니다.
# Data Subnet은 기본 Provider를 유지해 cluster/unmanaged를 계속 관리합니다.
provider "aws" {
  alias  = "rosa_shared_tags"
  region = var.aws_region

  ignore_tags {
    key_prefixes = ["kubernetes.io/cluster/"]
  }

  default_tags {
    tags = {
      Project   = "seokpan"
      Phase     = "2"
      ManagedBy = "terraform"
      Component = "foundation"
    }
  }
}
