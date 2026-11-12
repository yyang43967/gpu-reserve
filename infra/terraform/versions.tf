terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Bucket and region from backend.hcl (`terraform init -backend-config=backend.hcl`),
  # the same file as the bootstrap's; see README.md.
  backend "s3" {
    key = "main/terraform.tfstate"
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  # Prefix for names that must be unique in the account.
  name = "${var.project}-${var.environment}"
  # Bucket names are global; the account id and region make them unique.
  bucket_suffix = "${local.account_id}-${var.region}"
  # Secrets the instance reads at deploy time, entered by hand (README.md).
  ssm_prefix = "/${var.project}/${var.environment}"
}
