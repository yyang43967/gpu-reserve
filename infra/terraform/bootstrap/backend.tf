# State lives in the bucket this configuration creates. The first apply runs with a
# local backend (backend_override.tf, not committed) and then migrates here; see
# ../README.md. Bucket and region come from backend.hcl (`terraform init
# -backend-config=backend.hcl`).
terraform {
  backend "s3" {
    key = "bootstrap/terraform.tfstate"
  }
}
