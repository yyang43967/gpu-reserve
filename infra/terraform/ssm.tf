# Settings the deploy script reads from Parameter Store together with the secrets.
# Only values Terraform already knows live here, as plain Strings. Secrets
# (SecureString) are entered by hand so their values never reach Terraform state; see
# infra/deploy/README.md.

locals {
  deploy_settings = {
    AWS_REGION        = var.region
    API_SITE_ADDRESS  = var.api_hostname
    CORS_ORIGINS      = var.frontend_origin
    FRONTEND_URL      = var.frontend_origin
    EMAIL_FROM        = "GPU Reserve <no-reply@${var.email_domain}>"
    S3_BUCKET_UPLOADS = aws_s3_bucket.this["uploads"].bucket
    BACKUPS_BUCKET    = aws_s3_bucket.this["backups"].bucket
  }
}

resource "aws_ssm_parameter" "deploy_setting" {
  for_each = local.deploy_settings
  name     = "${local.ssm_prefix}/${each.key}"
  type     = "String"
  value    = each.value
}
