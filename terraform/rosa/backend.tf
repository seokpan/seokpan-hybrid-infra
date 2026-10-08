terraform {
  backend "s3" {
    key                  = "phase2/rosa/terraform.tfstate"
    workspace_key_prefix = "phase2/rosa/env"
    region               = "ap-northeast-2"
    encrypt              = true
    use_lockfile         = true
  }
}
