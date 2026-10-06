terraform {
  backend "s3" {
    # 실제 State 버킷 이름은 init 시 -backend-config로 전달합니다.
    key                  = "phase2/foundation/terraform.tfstate"
    workspace_key_prefix = "phase2/foundation/env"
    region               = "ap-northeast-2"
    encrypt              = true
    use_lockfile         = true
  }
}
