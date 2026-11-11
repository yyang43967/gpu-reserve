# Deployment

The backend runs on one EC2 instance as a Docker Compose project: Caddy (TLS for `api.gpu.yiyangdev.com`), the API, the ARQ worker, Postgres with pgvector, and Redis. The infrastructure comes from `infra/terraform`.

| File | Where it runs |
|---|---|
| `release.sh` | A developer machine (for now) or CI: builds and pushes the image, starts a deploy, checks the result |
| `deploy.sh` | The instance, as root, through SSM Run Command |
| `backup.sh`, `restore.sh` | The instance: daily from a systemd timer, before each migration, and by hand |
| `alert.sh` | The instance: emails the journal of a failed backup through SES |
| `lib.sh` | Shared by the three scripts above |
| `compose.yml`, `Caddyfile`, `systemd/` | The instance, from the release being deployed |

These files are copied into the backend image at `/app/deploy`. A deploy extracts them from the image it is deploying, so the configuration always matches the code, and a rollback brings back the configuration of that release too.

## Secrets and settings

`deploy.sh` turns every parameter under `/gpu-reserve/prod/` into the Compose env file (`/opt/gpu-reserve/.env`, mode 0600, never printed). Terraform creates the non-secret ones (`AWS_REGION`, `API_SITE_ADDRESS`, `CORS_ORIGINS`, `FRONTEND_URL`, `EMAIL_FROM`, `S3_BUCKET_UPLOADS`, `BACKUPS_BUCKET`). Enter the rest by hand, as SecureString with the default key:

| Name | Required | Value |
|---|---|---|
| `POSTGRES_PASSWORD` | yes | `openssl rand -hex 32` (hex only: it goes into a connection URL) |
| `JWT_SECRET` | yes | `openssl rand -hex 32` (at least 32 bytes) |
| `OPENAI_API_KEY` | for the assistant | Embeddings for policy search, and the default model. Without it the assistant is off. |
| `ANTHROPIC_API_KEY` | no | Only for an `anthropic:` model |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | no | Tracing; also set `LANGFUSE_HOST` (String) |
| `ASSISTANT_MODEL`, `LOG_LEVEL` | no | String; defaults `openai:gpt-5.4-mini-2026-03-17` and `INFO` |

Values may not contain a single quote or a newline; the deploy refuses them.

```bash
export AWS_PROFILE=gpu-reserve AWS_REGION=us-east-2
put() { aws ssm put-parameter --name "/gpu-reserve/prod/$1" --type SecureString --overwrite --value "$2" >/dev/null; }
put POSTGRES_PASSWORD "$(openssl rand -hex 32)"
put JWT_SECRET "$(openssl rand -hex 32)"
read -rs KEY && put OPENAI_API_KEY "$KEY"; unset KEY   # paste the key, then Enter
```

`POSTGRES_PASSWORD` is used when Postgres first creates its data directory. Changing the parameter later does not change the database's password.

## Deploy

From CI (`.github/workflows/deploy.yml`), once the account allows OIDC: after CI passes on `main`, the workflow assumes the deploy role with GitHub's OIDC token and runs `release.sh push` and `release.sh deploy <sha>`. A manual run with a SHA deploys that image without building (rollback). Until then, from a developer machine:

```bash
export AWS_PROFILE=gpu-reserve AWS_REGION=us-east-2
infra/deploy/release.sh push                  # builds HEAD for linux/arm64, prints its SHA
infra/deploy/release.sh deploy <sha>
```

`push` refuses a working tree with uncommitted changes, so the tag always names a commit. It skips the build if that SHA is already in ECR (tags are immutable).

`deploy` runs these steps on the instance and stops at the first failure, printing the containers' state and the last application log lines:

1. Log in to ECR, pull the image, and extract its `/app/deploy` into `/opt/gpu-reserve/releases/<sha>`.
2. Write the env file from Parameter Store.
3. Pull every image; start Postgres and Redis and wait until they are healthy.
4. Back up the database to S3 (`pre-deploy-<sha>`).
5. Run `alembic upgrade head` in a one-off container. If it fails, the running API and worker are untouched.
6. Start the whole stack and wait until every health check passes; check that the API runs the new image.
7. Point `/opt/gpu-reserve/current` at the release, install the backup timer from it, and append to `/opt/gpu-reserve/deploy-history`.

Deploys, backups, and restores share one lock, so they never overlap; a daily backup waits for a deploy in progress.

Then `release.sh` checks `https://api.gpu.yiyangdev.com/api/v1/ready` from outside.

## Rollback

Deploy an earlier SHA:

```bash
infra/deploy/release.sh tags                  # pushed tags, newest first
infra/deploy/release.sh deploy <earlier-sha>
```

ECR keeps the last 10 images. Migrations are not reversed, so rollback relies on one rule for every schema change: **expand, then contract.** Never drop or rename a column or table in the same release that stops using it. Add first; stop using it in a release; drop it in a later one. A rename is add, backfill, switch, then drop, across releases. New columns are nullable or have a server default. Then the previous release always runs against the newer schema.

If a migration itself damaged data, restore the `pre-deploy-<sha>` backup taken just before it (below), then deploy the earlier SHA.

## Backups and restore

`backup.sh` runs `pg_dump` (custom format, compressed) inside the Postgres container, checks that `pg_restore` can read the dump back, and uploads it to `s3://<backups bucket>/postgres/<UTC time>-<reason>.dump`. The bucket expires backups after 14 days. It runs:

- daily at 09:30 UTC (up to 15 minutes later), from `gpu-reserve-backup.timer`, which each deploy installs; after downtime it runs at the next boot;
- before every deploy's migrations;
- before every restore.

Restore, on the instance:

```bash
sudo /opt/gpu-reserve/current/restore.sh latest --confirm
sudo /opt/gpu-reserve/current/restore.sh postgres/2026-11-12T093012Z-daily.dump --confirm
```

It downloads and checks the dump, backs up the current database first (`pre-restore`; add `--no-safety-backup` only if the current database cannot be dumped), stops the API and worker, recreates the database, restores it, empties Redis (its availability cache, job queue, and rate-limit counters describe the old data), and starts the API and worker again. The site answers 502 for the minute this takes.

A failed run starts `gpu-reserve-alert@gpu-reserve-backup.service` (`OnFailure=`), which emails the last 40 journal lines of the backup to `ALERT_EMAIL` through SES with the instance role. The address is verified in SES by Terraform, so the mail arrives while the account is in the sandbox.

`tests/alert-test.sh` checks this under real systemd: it makes the backup fail, waits for the alert unit, and inspects the SES request a stand-in `aws` received. CI runs it on the runner; locally it needs a systemd container:

```bash
docker run -d --name alert-test --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock -v "$PWD:/src:ro" <ubuntu image with systemd and python3> /lib/systemd/systemd
docker exec alert-test bash /src/infra/deploy/tests/alert-test.sh
docker rm -f alert-test
```

Check the schedule and the last runs:

```bash
systemctl list-timers gpu-reserve-backup.timer
journalctl -u gpu-reserve-backup.service --since -3d
aws s3 ls "s3://$(cd infra/terraform && terraform output -raw backups_bucket)/postgres/"   # from a laptop
```

## Operating the instance

```bash
aws ssm start-session --target "$(cd infra/terraform && terraform output -raw instance_id)"
sudo -i
cd /opt/gpu-reserve/current
docker compose -p gpu-reserve --env-file /opt/gpu-reserve/.env ps
docker compose -p gpu-reserve --env-file /opt/gpu-reserve/.env logs --tail 100 api
tail /opt/gpu-reserve/deploy-history
```

Containers restart on their own after a reboot or a crash (`restart: unless-stopped`). After the account's spend limit paused the project, start the instance again (`infra/terraform/README.md`); the containers follow.

## Memory on a t4g.small

| Container | Limit | Measured |
|---|---|---|
| postgres | 512 MiB | 37 MiB |
| api | 448 MiB | 206 MiB |
| worker | 256 MiB | 89 MiB |
| redis | 96 MiB | 5 MiB |
| caddy | 64 MiB | 21 MiB |
| migrate (one-off, during deploys) | 256 MiB | n/a |
| **Total** | **1,376 MiB** | **about 360 MiB** |

Measured locally on this Compose file after seeding the demo data and 330 requests (availability, reservations, auth), without an OpenAI key. Postgres grows toward its 128 MB of shared buffers as real data is read. The instance has about 1.85 GiB usable; the OS, Docker, and the SSM agent take roughly 300 MiB. Containers get no swap; the host's 1 GiB swap file covers deploy-time spikes (the migration container and image extraction).
