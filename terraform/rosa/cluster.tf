resource "rhcs_cluster_rosa_classic" "cluster" {
  count          = var.cluster_enabled ? 1 : 0
  name           = var.cluster_name
  cloud_region   = "ap-northeast-2"
  aws_account_id = var.foundation.account_id
  version        = var.openshift_version
  channel_group  = "stable"

  multi_az             = true
  replicas             = 3
  compute_machine_type = "m5.xlarge"
  worker_disk_size     = var.worker_disk_size_gib
  autoscaling_enabled  = false

  aws_subnet_ids = concat(
    [for key in sort(keys(var.foundation.subnets)) : var.foundation.subnets[key].public_id],
    [for key in sort(keys(var.foundation.subnets)) : var.foundation.subnets[key].rosa_private_id],
  )
  availability_zones = [for key in sort(keys(var.foundation.subnets)) : var.foundation.subnets[key].availability_zone]
  machine_cidr       = "192.168.64.0/20"
  pod_cidr           = "10.128.0.0/14"
  service_cidr       = "10.240.0.0/16"
  host_prefix        = 23

  private          = false
  aws_private_link = false
  sts = {
    role_arn         = var.foundation.account_roles.installer
    support_role_arn = var.foundation.account_roles.support
    instance_iam_roles = {
      master_role_arn = var.foundation.account_roles.controlplane
      worker_role_arn = var.foundation.account_roles.worker
    }
    operator_role_prefix     = var.operator_role_prefix
    oidc_config_id           = rhcs_rosa_oidc_config.cluster.id
    trust_policy_external_id = var.foundation.trust_policy_external_id
  }

  # Admin/IDP credentials are supplied using the approved separate procedure.
  create_admin_user = false
  properties        = { rosa_creator_arn = data.aws_caller_identity.current.arn }
  tags = {
    Project   = "seokpan"
    Phase     = "2"
    ManagedBy = "terraform"
    Component = "rosa"
  }

  disable_scp_checks                  = false
  wait_for_create_complete            = true
  disable_waiting_in_destroy          = false
  max_cluster_wait_timeout_in_minutes = 60
  destroy_timeout                     = 60

  depends_on = [
    terraform_data.input_contract,
    aws_iam_openid_connect_provider.cluster,
    aws_iam_role_policy_attachment.operator,
  ]
}
