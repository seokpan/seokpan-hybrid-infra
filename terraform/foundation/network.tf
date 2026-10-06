locals {
  network_cidrs = {
    az_a = {
      public = "192.168.64.0/24"
      rosa   = "192.168.67.0/24"
      data   = "192.168.70.0/24"
    }
    az_b = {
      public = "192.168.65.0/24"
      rosa   = "192.168.68.0/24"
      data   = "192.168.71.0/24"
    }
    az_c = {
      public = "192.168.66.0/24"
      rosa   = "192.168.69.0/24"
      data   = "192.168.72.0/24"
    }
  }
}

resource "aws_vpc" "main" {
  cidr_block           = "192.168.64.0/20"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name      = "seokpan-fnd-vpc"
    Component = "network"
  }
}

resource "aws_subnet" "public" {
  for_each = var.network_az_ids

  vpc_id                  = aws_vpc.main.id
  availability_zone_id    = each.value
  cidr_block              = local.network_cidrs[each.key].public
  map_public_ip_on_launch = false

  tags = {
    Name                     = "seokpan-fnd-public-${each.key}"
    "kubernetes.io/role/elb" = "1"
    Component                = "network"
  }
}

resource "aws_subnet" "rosa" {
  for_each = var.network_az_ids

  vpc_id                  = aws_vpc.main.id
  availability_zone_id    = each.value
  cidr_block              = local.network_cidrs[each.key].rosa
  map_public_ip_on_launch = false

  tags = {
    Name                              = "seokpan-fnd-rosa-${each.key}"
    "kubernetes.io/role/internal-elb" = "1"
    Component                         = "network"
  }
}

resource "aws_subnet" "data" {
  for_each = var.network_az_ids

  vpc_id                  = aws_vpc.main.id
  availability_zone_id    = each.value
  cidr_block              = local.network_cidrs[each.key].data
  map_public_ip_on_launch = false

  tags = {
    Name      = "seokpan-fnd-data-${each.key}"
    Component = "network"
  }
}
