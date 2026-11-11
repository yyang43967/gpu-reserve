variable "region" {
  description = "AWS region for every resource."
  type        = string
}

variable "project" {
  description = "Prefix for resource names and the Project tag."
  type        = string
  default     = "gpu-reserve"
}

variable "environment" {
  description = "Environment name, used in resource names and the SSM parameter path."
  type        = string
  default     = "prod"
}

variable "api_hostname" {
  description = "Public hostname of the API (Caddy obtains its certificate)."
  type        = string
}

variable "frontend_origin" {
  description = "Origin of the frontend, allowed by the uploads bucket's CORS rules."
  type        = string
}

variable "email_domain" {
  description = "Domain verified in SES for outgoing mail (DKIM records are an output)."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type. Must be arm64 (Graviton): the images are built for it."
  type        = string
  default     = "t4g.small"
}

variable "root_volume_gb" {
  description = "Size of the gp3 root volume, which also holds Postgres data and images."
  type        = number
  default     = 30
}

variable "budget_limit_usd" {
  description = "Monthly cost budget. Alerts at the first threshold and at the limit."
  type        = number
  default     = 20
}

variable "budget_first_alert_usd" {
  description = "Actual spend that triggers the first budget alert."
  type        = number
  default     = 10
}

variable "budget_alert_emails" {
  description = "Addresses that receive budget alerts."
  type        = list(string)
}

variable "backup_retention_days" {
  description = "Days a database backup is kept in the backups bucket."
  type        = number
  default     = 14
}

variable "allow_data_destroy" {
  description = "Lets `terraform destroy` delete buckets that still hold uploads or backups. Set to true only to tear the environment down for good."
  type        = bool
  default     = false
}

variable "github_deploy_role_enabled" {
  description = "Create GitHub's OIDC provider and the CI deploy role. Off while the account refuses OIDC providers."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "owner/name of the repository whose workflows may deploy."
  type        = string
  default     = "yyang43967/gpu-reserve"
}

variable "github_deploy_environment" {
  description = "GitHub environment that deploy jobs run in; only its jobs can assume the deploy role."
  type        = string
  default     = "production"
}

variable "ecr_images_kept" {
  description = "Images kept in the ECR repository; older ones are expired."
  type        = number
  default     = 10
}

variable "ses_sandbox_recipients" {
  description = "Addresses to verify in SES so demo mail reaches them while the account is in the sandbox."
  type        = list(string)
  default     = []
}
