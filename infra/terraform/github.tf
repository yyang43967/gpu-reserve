# GitHub Actions deploys through OIDC: a workflow job in the repository's production
# environment exchanges its token for this role's short-lived credentials. No access
# keys are stored in GitHub.
#
# Off by default: on AWS's Free plan the account's service control policy refuses
# IAM OpenID Connect providers. Until it is enabled, deploys run from a laptop.

locals {
  github_oidc_host = "token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.github_deploy_role_enabled ? 1 : 0
  url            = "https://${local.github_oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_assume" {
  count = var.github_deploy_role_enabled ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Only jobs that run in the protected environment, not any branch or pull request.
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_deploy_environment}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count                = var.github_deploy_role_enabled ? 1 : 0
  name                 = "${local.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_assume[0].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  count = var.github_deploy_role_enabled ? 1 : 0

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  # Run the deploy script on this instance only, through the stock shell document.
  statement {
    sid     = "RunDeploy"
    actions = ["ssm:SendCommand"]
    resources = [
      aws_instance.api.arn,
      "arn:${local.partition}:ssm:${var.region}::document/AWS-RunShellScript",
    ]
  }

  statement {
    sid       = "ReadDeployResult"
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }

  # release.sh finds the instance by its Name tag. DescribeInstances supports no
  # resource-level permissions, so it cannot be narrowed to the instance.
  statement {
    sid       = "FindInstance"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  # The hostname for the ready check after a deploy. A plain String, so no KMS access.
  statement {
    sid       = "ReadSiteAddress"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.deploy_setting["API_SITE_ADDRESS"].arn]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = var.github_deploy_role_enabled ? 1 : 0
  name   = "deploy"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}
