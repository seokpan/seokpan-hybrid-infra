# Synthetic full-Root plan tests. Every AWS/RHCS provider is mocked. These are
# Source assertions, not a real account/support/Plan/Apply/federation result.
mock_provider "aws" {
  override_during = plan

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:sts::123456789012:assumed-role/seokpan-tf-rosa/mock-source-test"
    }
  }
  mock_data "aws_vpc" {
    defaults = {
      cidr_block           = "192.168.64.0/20"
      enable_dns_support   = true
      enable_dns_hostnames = true
    }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = {
      arn = "arn:aws:iam::123456789012:oidc-provider/issuer.fixture.test/mock-cluster"
    }
  }
}

mock_provider "rhcs" {
  override_during = plan
}

# Override the complete computed collection. mock_data defaults are per-item
# templates for nested collections, so a six-object list belongs in values.
override_data {
  target = data.rhcs_rosa_operator_roles.cluster
  values = {
    operator_iam_roles = [
      {
        role_name          = "mock-operator-1"
        policy_name        = "mock-policy-1"
        operator_namespace = "mock-namespace-1"
        operator_name      = "mock-name-1"
        service_accounts   = ["system:serviceaccount:mock-namespace-1:mock-sa-1"]
      },
      {
        role_name          = "mock-operator-2"
        policy_name        = "mock-policy-2"
        operator_namespace = "mock-namespace-2"
        operator_name      = "mock-name-2"
        service_accounts   = ["system:serviceaccount:mock-namespace-2:mock-sa-2"]
      },
      {
        role_name          = "mock-operator-3"
        policy_name        = "mock-policy-3"
        operator_namespace = "mock-namespace-3"
        operator_name      = "mock-name-3"
        service_accounts   = ["system:serviceaccount:mock-namespace-3:mock-sa-3"]
      },
      {
        role_name          = "mock-operator-4"
        policy_name        = "mock-policy-4"
        operator_namespace = "mock-namespace-4"
        operator_name      = "mock-name-4"
        service_accounts   = ["system:serviceaccount:mock-namespace-4:mock-sa-4"]
      },
      {
        role_name          = "mock-operator-5"
        policy_name        = "mock-policy-5"
        operator_namespace = "mock-namespace-5"
        operator_name      = "mock-name-5"
        service_accounts   = ["system:serviceaccount:mock-namespace-5:mock-sa-5"]
      },
      {
        role_name          = "mock-operator-6"
        policy_name        = "mock-policy-6"
        operator_namespace = "mock-namespace-6"
        operator_name      = "mock-name-6"
        service_accounts   = ["system:serviceaccount:mock-namespace-6:mock-sa-6a", "system:serviceaccount:mock-namespace-6:mock-sa-6b"]
      },
    ]
  }
}

override_data {
  target = data.aws_subnet.public["az_a"]
  values = { vpc_id = "vpc-00000001", cidr_block = "192.168.64.0/24", availability_zone = "ap-northeast-2a" }
}
override_data {
  target = data.aws_subnet.public["az_b"]
  values = { vpc_id = "vpc-00000001", cidr_block = "192.168.65.0/24", availability_zone = "ap-northeast-2b" }
}
override_data {
  target = data.aws_subnet.public["az_c"]
  values = { vpc_id = "vpc-00000001", cidr_block = "192.168.66.0/24", availability_zone = "ap-northeast-2c" }
}
override_data {
  target = data.aws_subnet.rosa_private["az_a"]
  values = { vpc_id = "vpc-00000001", cidr_block = "192.168.67.0/24", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.rosa_private["az_b"]
  values = { vpc_id = "vpc-00000001", cidr_block = "192.168.68.0/24", availability_zone = "ap-northeast-2b", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.rosa_private["az_c"]
  values = { vpc_id = "vpc-00000001", cidr_block = "192.168.69.0/24", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_iam_role.account["installer"]
  values = { arn = "arn:aws:iam::123456789012:role/mock-installer" }
}
override_data {
  target = data.aws_iam_role.account["support"]
  values = { arn = "arn:aws:iam::123456789012:role/mock-support" }
}
override_data {
  target = data.aws_iam_role.account["controlplane"]
  values = { arn = "arn:aws:iam::123456789012:role/mock-controlplane" }
}
override_data {
  target = data.aws_iam_role.account["worker"]
  values = { arn = "arn:aws:iam::123456789012:role/mock-worker" }
}

run "issuer_without_scheme" {
  command = plan

  override_resource {
    target          = rhcs_rosa_oidc_config.cluster
    override_during = plan
    values = {
      id                = "mock-oidc-config"
      oidc_endpoint_url = "issuer.fixture.test/mock-cluster"
      thumbprint        = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  }

  assert {
    condition = (
      aws_iam_openid_connect_provider.cluster.url == "https://issuer.fixture.test/mock-cluster" &&
      toset(aws_iam_openid_connect_provider.cluster.client_id_list) == toset(["openshift", "sts.amazonaws.com"])
    )
    error_message = "Bare issuer must yield one HTTPS provider URL and preserve the ROSA client IDs."
  }
  assert {
    condition = (
      length(local.operator_roles) == 6 &&
      length(aws_iam_role.operator) == 6 &&
      length(aws_iam_role_policy_attachment.operator) == 6 &&
      alltrue([for name, role in aws_iam_role.operator :
        jsondecode(role.assume_role_policy).Version == "2012-10-17" &&
        length(jsondecode(role.assume_role_policy).Statement) == 1 &&
        jsondecode(role.assume_role_policy).Statement[0].Effect == "Allow" &&
        jsondecode(role.assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity" &&
        jsondecode(role.assume_role_policy).Statement[0].Principal.Federated == aws_iam_openid_connect_provider.cluster.arn &&
        toset(keys(jsondecode(role.assume_role_policy).Statement[0].Condition)) == toset(["ForAnyValue:StringEquals"]) &&
        toset(keys(jsondecode(role.assume_role_policy).Statement[0].Condition["ForAnyValue:StringEquals"])) == toset(["issuer.fixture.test/mock-cluster:sub"]) &&
        toset(jsondecode(role.assume_role_policy).Statement[0].Condition["ForAnyValue:StringEquals"]["issuer.fixture.test/mock-cluster:sub"]) == toset(local.operator_roles[name].service_accounts)
      ]) &&
      alltrue([for name, attachment in aws_iam_role_policy_attachment.operator :
        attachment.role == aws_iam_role.operator[name].name &&
        attachment.policy_arn == var.foundation.operator_policy_arns[local.operator_roles[name].policy_name]
      ])
    )
    error_message = "All six generated Role trust JSONs, subjects and policy attachments must preserve the Classic mapping."
  }
}

run "issuer_with_https_scheme" {
  command = plan

  override_resource {
    target          = rhcs_rosa_oidc_config.cluster
    override_during = plan
    values = {
      id                = "mock-oidc-config"
      oidc_endpoint_url = "https://issuer.fixture.test/mock-cluster"
      thumbprint        = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  }

  assert {
    condition = (
      aws_iam_openid_connect_provider.cluster.url == "https://issuer.fixture.test/mock-cluster" &&
      toset(aws_iam_openid_connect_provider.cluster.client_id_list) == toset(["openshift", "sts.amazonaws.com"])
    )
    error_message = "HTTPS issuer must yield the same provider URL without a duplicated scheme."
  }
  assert {
    condition = (
      length(local.operator_roles) == 6 &&
      length(aws_iam_role.operator) == 6 &&
      length(aws_iam_role_policy_attachment.operator) == 6 &&
      alltrue([for name, role in aws_iam_role.operator :
        jsondecode(role.assume_role_policy).Version == "2012-10-17" &&
        length(jsondecode(role.assume_role_policy).Statement) == 1 &&
        jsondecode(role.assume_role_policy).Statement[0].Effect == "Allow" &&
        jsondecode(role.assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity" &&
        jsondecode(role.assume_role_policy).Statement[0].Principal.Federated == aws_iam_openid_connect_provider.cluster.arn &&
        toset(keys(jsondecode(role.assume_role_policy).Statement[0].Condition)) == toset(["ForAnyValue:StringEquals"]) &&
        toset(keys(jsondecode(role.assume_role_policy).Statement[0].Condition["ForAnyValue:StringEquals"])) == toset(["issuer.fixture.test/mock-cluster:sub"]) &&
        toset(jsondecode(role.assume_role_policy).Statement[0].Condition["ForAnyValue:StringEquals"]["issuer.fixture.test/mock-cluster:sub"]) == toset(local.operator_roles[name].service_accounts)
      ]) &&
      alltrue([for name, attachment in aws_iam_role_policy_attachment.operator :
        attachment.role == aws_iam_role.operator[name].name &&
        attachment.policy_arn == var.foundation.operator_policy_arns[local.operator_roles[name].policy_name]
      ])
    )
    error_message = "HTTPS input must preserve the same six generated trust policies and mapped attachments."
  }
}
