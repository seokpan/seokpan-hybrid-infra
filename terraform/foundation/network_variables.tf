variable "network_az_ids" {
  description = "Three AZ IDs for the approved network layout; verify service support before apply."
  type        = map(string)

  default = {
    az_a = "apne2-az1"
    az_b = "apne2-az2"
    az_c = "apne2-az3"
  }

  validation {
    condition = (
      toset(keys(var.network_az_ids)) == toset(["az_a", "az_b", "az_c"]) &&
      length(toset(values(var.network_az_ids))) == 3
    )
    error_message = "Use az_a, az_b and az_c with three distinct AZ IDs."
  }
}
