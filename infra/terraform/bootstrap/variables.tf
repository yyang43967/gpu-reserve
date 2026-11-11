variable "region" {
  description = "AWS region for the state bucket. Use the same region as the main configuration."
  type        = string
}

variable "project" {
  description = "Prefix for resource names and the Project tag."
  type        = string
  default     = "gpu-reserve"
}

variable "noncurrent_version_days" {
  description = "Days to keep superseded state versions."
  type        = number
  default     = 90
}
