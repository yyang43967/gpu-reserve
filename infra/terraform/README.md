# Infrastructure (Terraform)

Two configurations:

| Directory | What it manages | State |
|---|---|---|
| `bootstrap/` | The S3 bucket that holds Terraform state | In that bucket, key `bootstrap/terraform.tfstate` |
| `.` (added next) | Everything the application runs on | In that bucket, its own key |

Region: `us-east-2`. Every command below runs against one AWS account.

## Prerequisites

- Terraform 1.10 or later (native S3 state locking) and AWS CLI v2.
- Credentials from IAM Identity Center. No IAM users or access keys.

```bash
aws configure sso --profile gpu-reserve   # once: start URL, region, account, role
aws sso login --profile gpu-reserve       # when the session expires
export AWS_PROFILE=gpu-reserve
aws sts get-caller-identity               # check before any plan or apply
```

## Account notes

- **Region.** The account was created with AWS's newer sign-up flow, which starts it in a single region (`us-east-2`). Everything is deployed there.
- **Spend limit.** If the account's project has a monthly spend limit, reaching it pauses the project: new resources are blocked first, then idle resources are paused, and at the limit the whole project stops. Data is kept, but nothing restarts on its own. After raising the limit or reactivating the project:
  1. Start the EC2 instance (console or `aws ec2 start-instances --instance-ids <id>`).
  2. Docker starts the containers (`restart: unless-stopped`). Check `https://api.gpu.yiyangdev.com/api/v1/ready`.
  3. If a deploy was interrupted, run the deploy again for the current image tag.
- **Budgets** ignore credits, so they report what the month actually costs, not the net after credits.

## Bootstrap: the state bucket

Run once per account. The configuration creates the bucket its own state will live in, so the first apply uses local state and then moves it into the bucket.

The bucket is `gpu-reserve-tfstate-<account-id>-<region>`, with versioning, SSE-S3 encryption, public access blocked, a policy refusing non-TLS requests, and superseded state versions kept for 90 days. It costs well under $0.01 a month.

```bash
cd infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars

# 1. First run with local state. backend_override.tf is ignored by git.
printf 'terraform {\n  backend "local" {}\n}\n' > backend_override.tf
terraform init
terraform plan -out=bootstrap.tfplan
terraform apply bootstrap.tfplan

# 2. Move the state into the bucket.
terraform output -raw backend_config > backend.hcl
rm backend_override.tf
terraform init -migrate-state -backend-config=backend.hcl   # answer "yes"

# 3. Check, then remove the local copies.
aws s3 ls "s3://$(terraform output -raw state_bucket)/bootstrap/"
rm -f terraform.tfstate terraform.tfstate.backup bootstrap.tfplan
```

On another machine, `backend.hcl` is all that is needed: copy `backend.hcl.example`, fill in the account id, and run `terraform init -backend-config=backend.hcl`.

**If step 2 never happened and the local state is lost**, the bucket still exists. Recreate `backend.hcl` from the example and import it instead of creating a second one:

```bash
terraform init -backend-config=backend.hcl
terraform import aws_s3_bucket.state gpu-reserve-tfstate-<account-id>-us-east-2
terraform plan   # re-adopts the bucket's settings; review before applying
```

**Removing it.** The bucket has `prevent_destroy`, and destroying the main configuration leaves it alone. To close the project completely, destroy the main configuration first, then remove `prevent_destroy`, empty every object version from the bucket, and run `terraform destroy` here with a local backend.
