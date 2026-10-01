terraform {
  backend "s3" {
    bucket       = "seokpan-tfstate-847835841591"
    key          = "phase2/bootstrap/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
