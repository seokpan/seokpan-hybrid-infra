locals {
  az_slots = toset(["az_a", "az_b", "az_c"])
  subnet_cidrs = {
    az_a = { public = "192.168.64.0/24", rosa_private = "192.168.67.0/24" }
    az_b = { public = "192.168.65.0/24", rosa_private = "192.168.68.0/24" }
    az_c = { public = "192.168.66.0/24", rosa_private = "192.168.69.0/24" }
  }
  account_role_names = {
    for key, arn in var.foundation.account_roles : key => element(reverse(split("/", arn)), 0)
  }
}

data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition = (
        self.account_id == var.foundation.account_id &&
        can(regex("^arn:aws:sts::${var.foundation.account_id}:assumed-role/seokpan-tf-rosa/[^/]+$", self.arn))
      )
      error_message = "The AWS provider must use the designated same-account seokpan-tf-rosa session."
    }
  }
}

data "aws_vpc" "foundation" {
  id = var.foundation.vpc_id
  lifecycle {
    postcondition {
      condition     = self.cidr_block == "192.168.64.0/20" && self.enable_dns_support && self.enable_dns_hostnames
      error_message = "The supplied VPC must match the approved CIDR and have DNS support/hostnames enabled."
    }
  }
}

data "aws_subnet" "public" {
  for_each = local.az_slots
  id       = var.foundation.subnets[each.key].public_id
  lifecycle {
    postcondition {
      condition = (
        self.vpc_id == var.foundation.vpc_id &&
        self.cidr_block == local.subnet_cidrs[each.key].public &&
        self.availability_zone == var.foundation.subnets[each.key].availability_zone
      )
      error_message = "A public subnet does not match the handed-off VPC/AZ; route/IGW inspection remains required."
    }
  }
}

data "aws_subnet" "rosa_private" {
  for_each = local.az_slots
  id       = var.foundation.subnets[each.key].rosa_private_id
  lifecycle {
    postcondition {
      condition = (
        self.vpc_id == var.foundation.vpc_id &&
        self.cidr_block == local.subnet_cidrs[each.key].rosa_private &&
        self.availability_zone == var.foundation.subnets[each.key].availability_zone &&
        !self.map_public_ip_on_launch
      )
      error_message = "A ROSA private subnet does not match VPC/AZ or assigns public IPs; NAT/routes require owner review."
    }
  }
}

data "aws_iam_role" "account" {
  for_each = toset(["installer", "support", "controlplane", "worker"])
  name     = local.account_role_names[each.key]
  lifecycle {
    postcondition {
      condition     = self.arn == var.foundation.account_roles[each.key]
      error_message = "The supplied foundation Account Role reference does not match the actual IAM role."
    }
  }
}

# Preconditions block creation; references still require genuine owner review.
resource "terraform_data" "input_contract" {
  input = {
    foundation_revision  = var.foundation.revision
    source_code_revision = var.foundation.source_code_revision
    confirmed_at         = var.foundation.confirmed_at
    execution_review     = var.execution_review
  }

  lifecycle {
    precondition {
      condition     = var.execution_review.foundation_revision == var.foundation.revision
      error_message = "Execution review and foundation input revisions differ. Re-export/review the allowlisted inputs."
    }
  }

  depends_on = [
    data.aws_caller_identity.current,
    data.aws_vpc.foundation,
    data.aws_subnet.public,
    data.aws_subnet.rosa_private,
    data.aws_iam_role.account,
  ]
}
