locals {
  binding_enabled    = var.worker_sg_binding != null
  data_binding_ports = local.binding_enabled ? { mariadb = 3306, redis = 6379 } : {}
}

data "aws_security_group" "worker" {
  count = local.binding_enabled ? 1 : 0
  id    = var.worker_sg_binding.worker_security_group_id
  lifecycle {
    postcondition {
      condition     = self.vpc_id == var.foundation.vpc_id && !contains(values(var.foundation.data_security_groups), self.id)
      error_message = "The observed Worker SG belongs to a different VPC or is one of the Data SGs."
    }
  }
}

data "aws_security_group" "data" {
  for_each = local.data_binding_ports
  id       = var.foundation.data_security_groups[each.key]
  lifecycle {
    postcondition {
      condition     = self.vpc_id == var.foundation.vpc_id
      error_message = "The supplied foundation Data SG belongs to a different VPC."
    }
  }
}

resource "aws_vpc_security_group_ingress_rule" "worker_to_data" {
  for_each                     = local.data_binding_ports
  security_group_id            = data.aws_security_group.data[each.key].id
  referenced_security_group_id = data.aws_security_group.worker[0].id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
  description                  = "Current ROSA Worker to ${each.key}; Binding owned by rosa State"

  lifecycle {
    precondition {
      condition     = var.cluster_enabled ? var.worker_sg_binding.cluster_id == rhcs_cluster_rosa_classic.cluster[0].id : false
      error_message = "Worker SG evidence refers to a different Cluster; inspect the recreated Cluster and replace the input."
    }
  }

  # Full-root destroy orders Binding before Cluster; partial replacement needs explicit removal.
  depends_on = [rhcs_cluster_rosa_classic.cluster]
}
