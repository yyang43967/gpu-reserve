output "instance_id" {
  description = "EC2 instance id (SSM Session Manager and SendCommand target)."
  value       = aws_instance.api.id
}

output "public_ip" {
  description = "Elastic IP of the instance."
  value       = aws_eip.api.public_ip
}

output "ecr_repository_url" {
  description = "Repository for the backend image; tags are git commit SHAs."
  value       = aws_ecr_repository.api.repository_url
}

output "uploads_bucket" {
  value = aws_s3_bucket.this["uploads"].bucket
}

output "backups_bucket" {
  value = aws_s3_bucket.this["backups"].bucket
}

output "ssm_parameter_prefix" {
  description = "Path under which the deploy script reads the application's secrets."
  value       = local.ssm_prefix
}

output "github_deploy_role_arn" {
  description = "Role for the CI deploy job, or null while it is disabled."
  value       = one(aws_iam_role.github_deploy[*].arn)
}

output "dns_records" {
  description = "Records to create in Cloudflare (DNS only, not proxied)."
  value = concat(
    [{ type = "A", name = var.api_hostname, value = aws_eip.api.public_ip }],
    [
      for token in aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens : {
        type  = "CNAME"
        name  = "${token}._domainkey.${var.email_domain}"
        value = "${token}.dkim.amazonses.com"
      }
    ],
    # Report-only to start; tighten to quarantine once the reports look clean.
    [{ type = "TXT", name = "_dmarc.${var.email_domain}", value = "v=DMARC1; p=none" }],
  )
}
