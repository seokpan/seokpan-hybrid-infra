terraform {
  backend "s3" {
    key          = "phase2/rosa/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
