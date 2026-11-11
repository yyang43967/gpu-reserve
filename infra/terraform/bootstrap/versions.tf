terraform {
  # Native S3 state locking (use_lockfile) needs 1.10 or later.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
