terraform {
  backend "s3" {
    bucket       = "seokpan-tfstate-847835841591"
    key          = "bootstrap/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
