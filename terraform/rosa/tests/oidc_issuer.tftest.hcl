# Two issuer plans in a scoped Source harness. Production OIDC/Trust/Attachment
# resource blocks are copied byte-for-byte; the RHCS Operator query is replaced
# by six typed synthetic roles because Terraform 1.16.4 cannot override that
# computed ListNestedAttribute collection. The original Root remains separately
# validated. These tests do not accept the actual query, foundation or Runtime.
mock_provider "aws" {
  override_during = plan

  mock_resource "aws_iam_openid_connect_provider" {
    defaults = {
      arn = "arn:aws:iam::123456789012:oidc-provider/issuer.fixture.test/mock-cluster"
    }
  }
}

mock_provider "rhcs" {
  override_during = plan
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
