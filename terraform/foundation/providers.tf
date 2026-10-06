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
