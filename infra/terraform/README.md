# Infrastructure (Terraform)

Two configurations:

| Directory | What it manages | State |
|---|---|---|
| `bootstrap/` | The S3 bucket that holds Terraform state | In that bucket, key `bootstrap/terraform.tfstate` |
| `.` | Everything the application runs on | In that bucket, key `main/terraform.tfstate` |

Region: `us-east-2`. Every command below runs against one AWS account.

## Prerequisites

- Terraform 1.10 or later (native S3 state locking) and AWS CLI v2.
- Credentials from the account's IAM Identity Center sign-in, short-lived. No IAM users or access keys.

```bash
aws login --profile gpu-reserve           # opens the browser; repeat when the session expires
export AWS_PROFILE=gpu-reserve AWS_REGION=us-east-2
aws sts get-caller-identity               # check before any plan or apply
```

Terraform's AWS provider reads the same profile. If a plan fails with an expired-token error, log in again.

## Account notes

- **Region.** The account was created with AWS's newer sign-up flow, which starts it in a single region (`us-east-2`). Everything is deployed there.
- **Plan.** The account is on AWS's Free plan and is applied as such. Upgrade to the Paid plan (console, "Upgrade plan") only if an apply fails on an operation the plan blocks, or before the free period ends on 2027-05-11; a Free-plan account is closed when it ends. Credits carry over and expire on 2027-11-11. Known block: IAM OpenID Connect providers are denied, so `github_deploy_role_enabled` stays `false` and deploys run from a laptop until the account is upgraded.
- **No spend limit.** Spend limits exist only on the Paid plan, and activating advanced features removes them and allows no new ones, so the account has none. The budgets alert; they stop nothing. If the instance is stopped (by hand, or by AWS):
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

## Main configuration

What it creates (`terraform plan` lists 38 resources with the CI deploy role off):

- The instance: `t4g.small`, Amazon Linux 2023 arm64, 30 GB encrypted gp3, IMDSv2 only, standard CPU credits, an Elastic IP, and a first-boot script that adds a 1 GiB swap file, Docker with log rotation, and the Compose plugin.
- A security group open on 80/tcp and 443/tcp+udp only. There is no SSH port: shell access is `aws ssm start-session --target <instance-id>`.
- The instance role: pull from ECR, read `/gpu-reserve/prod/*` parameters, use the uploads and backups buckets, send mail from the SES domain, and SSM management.
- ECR repository `gpu-reserve/api` (immutable tags, last 10 images kept).
- S3 buckets for uploads (CORS for the frontend origin) and backups (14-day expiry).
- The SES domain identity for `gpu.yiyangdev.com`, with DKIM.
- The non-secret deploy settings as String parameters under `/gpu-reserve/prod/` (secrets are added by hand).
- A $20 monthly cost budget, alerting at $10, at $20, and when the forecast passes $20.
- With `github_deploy_role_enabled = true`: GitHub's OIDC provider and the deploy role.

### Apply

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in budget_alert_emails
cp bootstrap/backend.hcl backend.hcl           # same bucket and region as the bootstrap
terraform init -backend-config=backend.hcl
terraform plan -out=main.tfplan
terraform apply main.tfplan
terraform output dns_records
```

Then, by hand:

1. In Cloudflare, add the records from `dns_records`, **DNS only** (grey cloud): the `A` record for the API, the three DKIM `CNAME`s, and the `_dmarc` `TXT`. Caddy needs the `A` record to reach the instance directly for its certificate.
2. Enter the secrets as SecureString parameters under `ssm_parameter_prefix` (the list is in `infra/deploy/README.md`; Terraform already created the non-secret settings there).
3. Deploy (see `infra/deploy/README.md`).

SES verifies the domain on its own once the DKIM records resolve (minutes to an hour). Addresses in `ses_sandbox_recipients` get a verification email from AWS; mail reaches them only after the link is clicked.

### Changing the instance

The AMI and the first-boot script are ignored after creation, so a newer Amazon Linux image never replaces the instance and its database. To rebuild deliberately: take a backup, `terraform apply -replace=aws_instance.api`, deploy, and restore the backup. The Elastic IP moves to the new instance, so DNS does not change.

### Destroy

Everything except the state bucket:

```bash
# Buckets that still hold uploads or backups are refused unless this is set first.
terraform apply -var allow_data_destroy=true
terraform destroy
```

The instance's root volume, with the database, is deleted with it. Download the latest backup first if the data matters. Remove the Cloudflare records afterwards. Rebuilding from scratch is the Apply section again; the database comes back from a backup.

### Turning on the CI deploy role

When the account allows IAM OIDC providers (check with `aws iam list-open-id-connect-providers`; on the Free plan it is denied):

1. Set `github_deploy_role_enabled = true` in `terraform.tfvars`, plan, and apply. It creates GitHub's OIDC provider and the deploy role.
2. In GitHub, create the `production` environment and limit its deployment branches to `main`. The role trusts only jobs that run in this environment.
3. Set the repository variables `AWS_REGION` (`us-east-2`), `AWS_DEPLOY_ROLE_ARN` (`terraform output -raw github_deploy_role_arn`), and `DEPLOY_ENABLED` (`true`). No secrets are needed.
4. Run the Deploy workflow by hand once, then let the next merge to `main` deploy on its own.

For the Evaluations workflow, create an `evals` environment with the secrets `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` (required reviewers recommended: a run costs money).
