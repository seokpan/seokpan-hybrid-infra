provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = [var.foundation.account_id]

  default_tags {
    tags = {
      Project   = "seokpan"
      Phase     = "2"
      ManagedBy = "terraform"
      Component = "rosa"
    }
  }
}

# RHCS_TOKEN is supplied to the provider environment, never a TF variable.
provider "rhcs" {}
