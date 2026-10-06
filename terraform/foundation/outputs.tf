output "vpc_id" {
  description = "Foundation VPC ID."
  value       = aws_vpc.main.id
}

output "network_subnets" {
  description = "Subnet IDs and AZ mapping; Data subnets are excluded from ROSA installation inputs."

  value = {
    for key in keys(var.network_az_ids) : key => {
      availability_zone    = aws_subnet.public[key].availability_zone
      availability_zone_id = aws_subnet.public[key].availability_zone_id
      public_id            = aws_subnet.public[key].id
      rosa_private_id      = aws_subnet.rosa[key].id
      data_private_id      = aws_subnet.data[key].id
    }
  }
}
