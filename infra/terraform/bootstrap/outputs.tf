output "state_bucket" {
  description = "Name of the state bucket."
  value       = aws_s3_bucket.state.bucket
}

output "backend_config" {
  description = "Settings for backend.hcl in this directory and in the main configuration (each with its own key)."
  value       = <<-EOT
    bucket       = "${aws_s3_bucket.state.bucket}"
    region       = "${var.region}"
    use_lockfile = true
    encrypt      = true
  EOT
}
