# Temporary load-test environment (loadtest/instance/README.md), off by default.
#
# - A twin of the production instance (same type, AMI, first-boot script), which runs
#   the production Compose stack with generated data. Production is never under load.
# - A k6 driver in the same subnet, so requests stay on private addresses.
# - A bucket for the source archive and the results, emptied on destroy.
#
# Neither instance accepts traffic from the internet. The twin admits the driver only:
# SSH (Docker over SSH, for the runner), Caddy, and the API's port for the
# direct-to-uvicorn arm. Both use unlimited CPU credits, so neither is throttled
# mid-run; the twin's baseline is simulated with a CPU cap instead
# (loadtest/compose.baseline.yml). Both get public addresses only to reach SSM, ECR,
# S3, and GitHub (k6) without a NAT gateway.

variable "loadtest_enabled" {
  description = "Create the temporary load-test twin, k6 driver, and results bucket."
  type        = bool
  default     = false
}

locals {
  loadtest       = var.loadtest_enabled ? toset(["on"]) : toset([])
  loadtest_name  = "${local.name}-loadtest"
  loadtest_hosts = var.loadtest_enabled ? { twin = var.instance_type, driver = "t4g.micro" } : {}
}

resource "aws_s3_bucket" "loadtest" {
  for_each      = local.loadtest
  bucket        = "${var.project}-loadtest-${local.bucket_suffix}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "loadtest" {
  for_each                = aws_s3_bucket.loadtest
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "loadtest" {
  for_each = aws_s3_bucket.loadtest
  bucket   = each.value.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_security_group" "loadtest" {
  for_each    = local.loadtest_hosts
  name        = "${local.loadtest_name}-${each.key}"
  description = each.key == "twin" ? "Load-test twin: the driver only" : "Load-test driver: no inbound"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "loadtest_twin" {
  for_each = var.loadtest_enabled ? {
    ssh    = 22
    caddy  = 80
    direct = 8000
  } : {}
  security_group_id            = aws_security_group.loadtest["twin"].id
  description                  = "From the k6 driver: ${each.key}"
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
  referenced_security_group_id = aws_security_group.loadtest["driver"].id
}

resource "aws_vpc_security_group_egress_rule" "loadtest" {
  for_each          = aws_security_group.loadtest
  security_group_id = each.value.id
  description       = "Outbound: SSM, ECR, S3, packages, the twin"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# Each role can be managed through SSM and use the results bucket. The twin can also
# pull the image; neither can read parameters, the application's buckets, or SES.
resource "aws_iam_role" "loadtest" {
  for_each           = local.loadtest_hosts
  name               = "${local.loadtest_name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "loadtest_ssm" {
  for_each   = aws_iam_role.loadtest
  role       = each.value.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "loadtest" {
  for_each = local.loadtest_hosts

  statement {
    sid       = "ResultsList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.loadtest["on"].arn]
  }

  statement {
    sid       = "ResultsObjects"
    actions   = each.key == "driver" ? ["s3:GetObject", "s3:PutObject"] : ["s3:GetObject"]
    resources = ["${aws_s3_bucket.loadtest["on"].arn}/*"]
  }

  dynamic "statement" {
    for_each = each.key == "twin" ? ["pull"] : []
    content {
      sid       = "EcrLogin"
      actions   = ["ecr:GetAuthorizationToken"]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "twin" ? ["pull"] : []
    content {
      sid = "EcrPull"
      actions = [
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
      ]
      resources = [aws_ecr_repository.api.arn]
    }
  }
}

resource "aws_iam_role_policy" "loadtest" {
  for_each = aws_iam_role.loadtest
  name     = "loadtest"
  role     = each.value.id
  policy   = data.aws_iam_policy_document.loadtest[each.key].json
}

resource "aws_iam_instance_profile" "loadtest" {
  for_each = aws_iam_role.loadtest
  name     = each.value.name
  role     = each.value.name
}

resource "aws_instance" "loadtest" {
  for_each                    = local.loadtest_hosts
  ami                         = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type               = each.value
  subnet_id                   = local.instance_subnet
  vpc_security_group_ids      = [aws_security_group.loadtest[each.key].id]
  iam_instance_profile        = aws_iam_instance_profile.loadtest[each.key].name
  associate_public_ip_address = true

  credit_specification {
    cpu_credits = "unlimited"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = each.key == "twin" ? var.root_volume_gb : 8
    encrypted             = true
    delete_on_termination = true
  }

  # The production first-boot script, so the twin's host matches (swap, Docker, the
  # Compose plugin); the driver needs the Docker client and Compose for the runner.
  user_data = templatefile("${path.module}/templates/user-data.sh.tftpl", {
    swap_mib        = 1024
    compose_version = "v5.7.1"
    compose_sha256  = "f2f49f903240895fa764c0dca3a14548b754ad9426f6b609ccaf55ec5a3aa2a5"
    app_dir         = "/opt/gpu-reserve-loadtest"
  })

  tags = {
    Name = "${local.loadtest_name}-${each.key}"
  }

  lifecycle {
    ignore_changes = [ami]
  }
}

output "loadtest" {
  description = "The load-test twin's private address, the instance ids, and the results bucket (null when off)."
  value = var.loadtest_enabled ? {
    twin_private_ip = aws_instance.loadtest["twin"].private_ip
    twin_id         = aws_instance.loadtest["twin"].id
    driver_id       = aws_instance.loadtest["driver"].id
    bucket          = aws_s3_bucket.loadtest["on"].id
  } : null
}
