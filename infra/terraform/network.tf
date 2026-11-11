# The default VPC and one of its public subnets. No NAT gateway and no load balancer:
# the instance has a public address, and Caddy terminates TLS on it.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_subnet" "default" {
  for_each = toset(data.aws_subnets.default.ids)
  id       = each.value
}

data "aws_ec2_instance_type_offerings" "instance" {
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

locals {
  # The first zone (alphabetically) that offers the instance type, so the choice is
  # stable across plans and never lands on a zone without capacity for the type.
  instance_subnet = [
    for zone in sort(data.aws_ec2_instance_type_offerings.instance.locations) :
    one([for s in data.aws_subnet.default : s.id if s.availability_zone == zone])
    if contains([for s in data.aws_subnet.default : s.availability_zone], zone)
  ][0]
}

resource "aws_security_group" "api" {
  name        = "${local.name}-api"
  description = "HTTP and HTTPS to Caddy. No SSH: access is through SSM Session Manager."
  vpc_id      = data.aws_vpc.default.id
}

# Port 80 serves ACME HTTP challenges and redirects to HTTPS.
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.api.id
  description       = "HTTP (ACME challenges, redirect to HTTPS)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each = {
    tcp = "tcp"
    # HTTP/3, which Caddy serves by default.
    udp = "udp"
  }
  security_group_id = aws_security_group.api.id
  description       = "HTTPS over ${each.value}"
  ip_protocol       = each.value
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.api.id
  description       = "Outbound: ECR, S3, SES, SSM, ACME, model APIs"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
