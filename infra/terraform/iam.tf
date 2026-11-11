# The instance role: pull images, read the environment's parameters, use the two
# buckets, send mail from the verified domain, and be managed through SSM.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${local.name}-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  statement {
    sid       = "ReadParameters"
    actions   = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"]
    resources = ["arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.ssm_prefix}/*"]
  }

  statement {
    sid       = "UploadObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.this["uploads"].arn}/*"]
  }

  # Without ListBucket, a missing object answers 403 instead of 404.
  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.this["uploads"].arn, aws_s3_bucket.this["backups"].arn]
  }

  statement {
    sid       = "Backups"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.this["backups"].arn}/*"]
  }

  statement {
    sid       = "SendMail"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["arn:${local.partition}:ses:${var.region}:${local.account_id}:identity/*"]

    # Only from the verified domain.
    condition {
      test     = "StringLike"
      variable = "ses:FromAddress"
      values   = ["*@${var.email_domain}"]
    }
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "application"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_iam_instance_profile" "api" {
  name = "${local.name}-instance"
  role = aws_iam_role.api.name
}
