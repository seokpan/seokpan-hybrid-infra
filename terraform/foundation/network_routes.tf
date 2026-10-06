resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name      = "seokpan-fnd-igw"
    Component = "network"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name      = "seokpan-fnd-public-rt"
    Component = "network"
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  for_each = var.network_az_ids

  domain = "vpc"

  tags = {
    Name      = "seokpan-fnd-nat-${each.key}"
    Component = "network"
  }
}

resource "aws_nat_gateway" "main" {
  for_each = var.network_az_ids

  allocation_id     = aws_eip.nat[each.key].id
  subnet_id         = aws_subnet.public[each.key].id
  connectivity_type = "public"

  depends_on = [aws_internet_gateway.main]

  tags = {
    Name      = "seokpan-fnd-nat-${each.key}"
    Component = "network"
  }
}

resource "aws_route_table" "rosa" {
  for_each = var.network_az_ids

  vpc_id = aws_vpc.main.id

  tags = {
    Name      = "seokpan-fnd-rosa-rt-${each.key}"
    Component = "network"
  }
}

resource "aws_route" "rosa_internet" {
  for_each = var.network_az_ids

  route_table_id         = aws_route_table.rosa[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[each.key].id
}

resource "aws_route_table_association" "rosa" {
  for_each = aws_subnet.rosa

  subnet_id      = each.value.id
  route_table_id = aws_route_table.rosa[each.key].id
}

# Data has no default Internet route.
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name      = "seokpan-fnd-data-rt"
    Component = "network"
  }
}

resource "aws_route_table_association" "data" {
  for_each = aws_subnet.data

  subnet_id      = each.value.id
  route_table_id = aws_route_table.data.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = concat(
    [aws_route_table.public.id],
    [for key in sort(keys(var.network_az_ids)) : aws_route_table.rosa[key].id]
  )

  tags = {
    Name      = "seokpan-fnd-s3-endpoint"
    Component = "network"
  }
}
