# Cluster-specific managed OIDC; account-wide IAM roles/policies remain foundation-owned.
resource "rhcs_rosa_oidc_config" "cluster" {
  managed    = true
  depends_on = [terraform_data.input_contract]
}

locals {
  # RHCS 1.7.7 returns host/path. Normalize an explicit HTTPS issuer too so
  # provider URL and IAM condition keys always derive from the same issuer.
  oidc_issuer_hostpath = trimprefix(rhcs_rosa_oidc_config.cluster.oidc_endpoint_url, "https://")
  oidc_issuer_url      = "https://${local.oidc_issuer_hostpath}"
}

resource "aws_iam_openid_connect_provider" "cluster" {
  url             = local.oidc_issuer_url
  client_id_list  = ["openshift", "sts.amazonaws.com"]
  thumbprint_list = [rhcs_rosa_oidc_config.cluster.thumbprint]
}

data "rhcs_rosa_operator_roles" "cluster" {
  operator_role_prefix = var.operator_role_prefix
  account_role_prefix  = var.foundation.account_role_prefix

  lifecycle {
    postcondition {
      condition = (
        length(self.operator_iam_roles) == 6 &&
        alltrue([for role in self.operator_iam_roles : contains(keys(var.foundation.operator_policy_arns), role.policy_name)])
      )
      error_message = "Inspect the actual Classic operator-role list and foundation policy map; do not guess missing policies."
    }
  }
}

locals {
  operator_roles = { for role in data.rhcs_rosa_operator_roles.cluster.operator_iam_roles : role.role_name => role }
}

resource "aws_iam_role" "operator" {
  for_each             = local.operator_roles
  name                 = each.value.role_name
  path                 = var.foundation.iam_path
  permissions_boundary = var.foundation.operator_permissions_boundary

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.cluster.arn }
      Condition = {
        StringEquals = {
          "${local.oidc_issuer_hostpath}:sub" = each.value.service_accounts
        }
      }
    }]
  })

  tags = {
    red-hat-managed    = "true"
    operator_namespace = each.value.operator_namespace
    operator_name      = each.value.operator_name
  }
}

resource "aws_iam_role_policy_attachment" "operator" {
  for_each   = local.operator_roles
  role       = aws_iam_role.operator[each.key].name
  policy_arn = var.foundation.operator_policy_arns[each.value.policy_name]
}
