output "platform_handoff" {
  description = "Owner exports only approved fields to protected inputs; this is not Runtime acceptance or an output-dump permission."
  sensitive   = true
  value = {
    schema_version      = 1
    foundation_revision = var.foundation.revision
    cluster_id          = var.cluster_enabled ? rhcs_cluster_rosa_classic.cluster[0].id : null
    infra_id            = var.cluster_enabled ? rhcs_cluster_rosa_classic.cluster[0].infra_id : null
    state               = var.cluster_enabled ? rhcs_cluster_rosa_classic.cluster[0].state : null
    current_version     = var.cluster_enabled ? rhcs_cluster_rosa_classic.cluster[0].current_version : null
    api_url             = var.cluster_enabled ? rhcs_cluster_rosa_classic.cluster[0].api_url : null
    console_url         = var.cluster_enabled ? rhcs_cluster_rosa_classic.cluster[0].console_url : null
    domain              = var.cluster_enabled ? rhcs_cluster_rosa_classic.cluster[0].domain : null
    worker_role_arn     = var.foundation.account_roles.worker
    binding_enabled     = local.binding_enabled
  }
}
