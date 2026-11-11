# The single instance. Postgres data lives on its root volume; backups go to S3
# (see storage.tf), so replacing the instance means restoring from the latest backup.

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "api" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type          = var.instance_type
  subnet_id              = local.instance_subnet
  vpc_security_group_ids = [aws_security_group.api.id]
  iam_instance_profile   = aws_iam_instance_profile.api.name

  # Standard mode: past the baseline, the CPU is throttled instead of billed for
  # surplus credits.
  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
    # Containers on Docker's bridge network are one hop further from the metadata
    # service; the API and worker need it for the instance role's credentials.
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/templates/user-data.sh.tftpl", {
    swap_mib        = 1024
    compose_version = "v5.7.1"
    compose_sha256  = "f2f49f903240895fa764c0dca3a14548b754ad9426f6b609ccaf55ec5a3aa2a5"
    app_dir         = "/opt/${var.project}"
  })

  tags = {
    Name = local.name
  }

  lifecycle {
    # A newer AMI or a changed first-boot script must not replace the instance and
    # its database. Rebuilding is a deliberate step (README.md).
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "api" {
  domain = "vpc"

  tags = {
    Name = local.name
  }
}

resource "aws_eip_association" "api" {
  instance_id   = aws_instance.api.id
  allocation_id = aws_eip.api.id
}
