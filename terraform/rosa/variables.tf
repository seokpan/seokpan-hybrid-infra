variable "foundation" {
  description = "Proposed allowlisted foundation handoff schema; A acceptance and real values are pending. Store the input outside Git."
  type = object({
    schema_version       = number
    revision             = string
    source_code_revision = string
    confirmed_at         = string
    terraform_version    = string
    aws_provider_version = string
    environment          = string
    account_id           = string
    region               = string
    vpc_id               = string
    subnets = map(object({
      availability_zone = string
      public_id         = string
      rosa_private_id   = string
    }))
    account_role_prefix = string
    iam_path            = string
    account_roles = object({
      installer    = string
      support      = string
      controlplane = string
      worker       = string
    })
    operator_policy_arns          = map(string)
    operator_permissions_boundary = optional(string, null)
    trust_policy_external_id      = optional(string, null)
    data_security_groups = object({
      mariadb = string
      redis   = string
    })
  })

  validation {
    condition = (
      var.foundation.schema_version == 1 &&
      can(regex("^[0-9a-f]{40}$", var.foundation.source_code_revision)) &&
      can(timecmp(var.foundation.confirmed_at, var.foundation.confirmed_at)) &&
      length(trimspace(var.foundation.revision)) > 0 &&
      var.foundation.terraform_version == "1.16.4" &&
      var.foundation.aws_provider_version == "6.67.0"
    )
    error_message = "Use schema 1, a full source SHA, RFC3339 confirmation time, revision, and the pinned generation combination."
  }

  validation {
    condition = (
      can(regex("^[0-9]{12}$", var.foundation.account_id)) &&
      var.foundation.region == "ap-northeast-2" &&
      var.foundation.environment == "cloud" &&
      can(regex("^vpc-[0-9a-f]{8}([0-9a-f]{9})?$", var.foundation.vpc_id)) &&
      toset(keys(var.foundation.subnets)) == toset(["az_a", "az_b", "az_c"]) &&
      length(distinct([for s in values(var.foundation.subnets) : s.availability_zone])) == 3 &&
      alltrue([for s in values(var.foundation.subnets) :
        can(regex("^ap-northeast-2[a-z]$", s.availability_zone)) &&
        can(regex("^subnet-[0-9a-f]{8}([0-9a-f]{9})?$", s.public_id)) &&
        can(regex("^subnet-[0-9a-f]{8}([0-9a-f]{9})?$", s.rosa_private_id))
      ]) &&
      length(distinct(flatten([for s in values(var.foundation.subnets) : [s.public_id, s.rosa_private_id]]))) == 6
    )
    error_message = "Use the cloud environment, Seoul account/VPC and distinct public/private subnet pairs in exactly three AZ slots."
  }

  validation {
    condition = (
      can(regex("^[A-Za-z0-9+=,.@_-]{1,32}$", var.foundation.account_role_prefix)) &&
      can(regex("^/([A-Za-z0-9+=,.@_-]+/)*$", var.foundation.iam_path)) &&
      alltrue([for arn in values(var.foundation.account_roles) :
        can(regex("^arn:aws:iam::${var.foundation.account_id}:role/.+$", arn))
      ]) &&
      length(distinct(values(var.foundation.account_roles))) == 4 &&
      length(var.foundation.operator_policy_arns) > 0 &&
      alltrue([for name, arn in var.foundation.operator_policy_arns :
        arn == "arn:aws:iam::${var.foundation.account_id}:policy${var.foundation.iam_path}${name}"
      ]) &&
      length(distinct(values(var.foundation.data_security_groups))) == 2 &&
      alltrue([for id in values(var.foundation.data_security_groups) :
        can(regex("^sg-[0-9a-f]{8}([0-9a-f]{9})?$", id))
      ])
    )
    error_message = "Account roles/operator policies must be explicit same-account references; Data SGs remain foundation-owned."
  }
}

variable "cluster_enabled" {
  type        = bool
  default     = true
  description = "Keep IAM/OIDC while deleting Cluster in a separate reviewed apply. Inspect actual service deletion before full Root cleanup."
}

variable "cluster_name" {
  type        = string
  description = "Reviewed project ROSA name, distinct from foundation naming."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,13}[a-z0-9]$", var.cluster_name))
    error_message = "Use a ROSA name of 2 to 15 lowercase letters/digits/hyphens, beginning with a letter."
  }
}

variable "operator_role_prefix" {
  type        = string
  description = "Cluster-specific prefix; do not reuse roles owned by another Cluster/State."
  validation {
    condition     = can(regex("^[A-Za-z0-9+=,.@_-]{1,32}$", var.operator_role_prefix))
    error_message = "Use a reviewed IAM-compatible operator prefix of 1 to 32 characters."
  }
}

variable "openshift_version" {
  type        = string
  description = "Exact supported stable 4.20 GA patch, obtained by the execution owner; no guessed default."
  validation {
    condition     = can(regex("^4\\.20\\.[0-9]+$", var.openshift_version))
    error_message = "Supply the exact supported 4.20 GA patch; a series, prerelease, or INPUT_REQUIRED is rejected."
  }
}

variable "worker_disk_size_gib" {
  type        = number
  description = "Reviewed service-supported Worker root volume size; no CP/Infra size inference."
  validation {
    condition     = var.worker_disk_size_gib > 0 && floor(var.worker_disk_size_gib) == var.worker_disk_size_gib
    error_message = "Supply a positive integral, service-supported disk size and include it in Cost Gate."
  }
}

variable "execution_review" {
  description = "Value-free input/support/preliminary Cost/window review references for the first owner Plan. Actual full Plan/Cost review is required before Apply."
  type = object({
    foundation_revision = string
    input_review        = string
    support_review      = string
    cost_review         = string
    window              = string
  })
  validation {
    condition = alltrue([for value in values(var.execution_review) :
      length(trimspace(value)) > 0 && !strcontains(upper(value), "INPUT_REQUIRED")
    ])
    error_message = "Complete the actual review references and execution window before a Cloud Plan; references are not independent proof."
  }
}

variable "worker_sg_binding" {
  description = "Stage 2: actual current Cluster ID and service-created Worker SG, independently inspected by B. Null creates no Data ingress."
  type = object({
    cluster_id               = string
    worker_security_group_id = string
    observed_at              = string
    evidence_reference       = string
  })
  default = null
  validation {
    condition = var.worker_sg_binding == null ? true : (
      length(trimspace(var.worker_sg_binding.cluster_id)) > 0 &&
      can(regex("^sg-[0-9a-f]{8}([0-9a-f]{9})?$", var.worker_sg_binding.worker_security_group_id)) &&
      can(timecmp(var.worker_sg_binding.observed_at, var.worker_sg_binding.observed_at)) &&
      length(trimspace(var.worker_sg_binding.evidence_reference)) > 0
    )
    error_message = "A Binding requires the actual current Cluster ID, SG ID, observation time and protected evidence reference."
  }
  validation {
    condition     = var.worker_sg_binding == null || var.cluster_enabled
    error_message = "Remove the Worker Data Binding in its own reviewed apply before disabling the Cluster; IAM/OIDC remain until real deletion is confirmed."
  }
}
