# Data 모듈 (foundation Root에서 호출하는 Local Module)
# - Provider 정확한 버전 고정은 호출하는 Root(foundation)의 required_providers와 lock 파일이 담당
# - 여기서는 이 모듈이 쓰는 기능의 최소 버전만 표시
#   (aws_elasticache_replication_group의 auth_token_wo는 AWS provider 6.62.0부터 지원)

terraform {
    required_providers {
        aws = {
            source = "hashicorp/aws"
            version = ">= 6.62.0"
        }
    }
}
