# Load test on a twin of the production instance

The laptop runs (`loadtest/results.md`) used CPU limits that add up to about a t4g.small, but on a faster, non-burstable host. This procedure runs the same scripts on real AWS hardware without touching production:

- **Twin:** a second t4g.small with production's AMI, first-boot script, release image, `infra/deploy/compose.yml`, and Caddyfile. Only `loadtest/compose.instance.yml` differs: `ENVIRONMENT=test` (the seed resets the database), the runner's cache settings, suppressed mail, and the API's port published for the driver.
- **Driver:** a t4g.micro in the same subnet running k6 and `run.py`. The runner drives the twin's Docker over SSH (`DOCKER_HOST=ssh://ec2-user@<twin>`), so it restarts, seeds, and samples the twin exactly as it does the local stack.
- **Results bucket:** the source archive going in, `loadtest/out` and logs coming out.

Production is not involved: separate instances, security groups, roles, and data. Both instances use unlimited credits, so neither is throttled mid-run; the baseline is simulated (below).

## Progress and resuming

Keep `loadtest/out/instance-<date>/PROGRESS.md` (ignored by Git) up to date: which phase is running or done, its label, the commands used, the image and commit, and a line of results per phase. Every phase also writes `loadtest/out/<UTC time>-<label>/` on the driver, synced to `s3://<bucket>/out/` when it ends, and its log to `s3://<bucket>/logs/<label>.log`. To resume, read the progress file, then `phase.sh status <label>` for the phase it names.

## Steps

All from the repository root on a laptop, with `export AWS_PROFILE=gpu-reserve AWS_REGION=us-east-2` after `aws login`. Every phase runs as a systemd unit on the driver, so an expired laptop session does not stop it; only the commands that start or check a phase need a valid session.

1. **Create** (approved first): set `loadtest_enabled = true` in `infra/terraform/terraform.tfvars`, `terraform plan`, review, `terraform apply`. Then `terraform output loadtest` gives the twin's private address and the bucket.
2. **Source:** `loadtest/instance/upload-source.sh <bucket>` (a clean tree; the archive carries its commit).
3. **Driver:** `loadtest/instance/remote.sh driver driver-setup.sh <bucket> <image URI> <twin IP>`. The image is the production tag, `<account>.dkr.ecr.us-east-2.amazonaws.com/gpu-reserve/api:<sha>`. It prints the driver's public key.
4. **Twin:** `loadtest/instance/remote.sh twin twin-setup.sh <bucket> <image URI> "<public key>"`. It prints Docker's cgroup driver (the baseline slice needs `systemd`, cgroup v2).
5. **Check:** `loadtest/instance/remote.sh driver phase.sh check` (Docker on the twin over SSH, and k6).
6. **Phases**, each started with `remote.sh driver phase.sh start <label> <bucket> [settings] -- <run.py options>` and followed with `remote.sh driver phase.sh status <label>`. `TWIN` in an option is replaced with the twin's address.

| Label | Settings and options | About |
|---|---|---|
| `smoke` | `-- --base-url http://TWIN:80 --scripts availability,slot_search,booking_race --repeats 1 --steps 3 --hold 4 --warmup 5s --measure 10s --skip-hit-rate` | 10 min |
| `burst` | `-- --base-url http://TWIN:80` (the full matrix: capacity, latency, race, hit rate; cache off and on; 3 repeats) | 75 min |
| `keepalive-a` | `-- --base-url http://TWIN:80 --configs cache-on --scripts slot_search,booking_race --skip-hit-rate` (production as it is) | 18 min |
| `keepalive-b` | `API_UPSTREAM_KEEPALIVE=60s API_KEEP_ALIVE_SECONDS=75 -- ` and the same options | 18 min |
| `keepalive-c` | `-- --base-url http://TWIN:8000 ...` and the same options (k6 straight to uvicorn, as on the laptop) | 18 min |
| `soak` | `-- --base-url http://TWIN:80 --soak 30m --soak-rates browse_servers=…,availability=…,slot_search=… --settle 10m` (rates: about half of each script's burst peak, together within the API's CPU) | 45 min |
| `baseline` | `-- --base-url http://TWIN:80 --compose-file loadtest/compose.baseline.yml --configs cache-on --capacity-only --repeats 1` | 15 min |

7. **Results:** `aws s3 sync s3://<bucket>/out/ loadtest/out/` and `aws s3 sync s3://<bucket>/logs/ loadtest/out/instance-<date>/logs/`, then `python3 loadtest/report.py loadtest/out/<run>` per phase.
8. **Teardown** (approved first): `loadtest_enabled = false`, `terraform plan` (it destroys exactly what step 1 created, the bucket with its contents), `terraform apply`. Then confirm nothing is left:

```bash
aws ec2 describe-instances --filters "Name=tag:Name,Values=gpu-reserve-prod-loadtest-*" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text      # terminated only
aws ec2 describe-volumes --filters Name=status,Values=available --query 'Volumes[].VolumeId'   # []
aws ec2 describe-addresses --query 'Addresses[].[PublicIp,InstanceId]' --output text           # production's only
aws ec2 describe-security-groups --filters "Name=group-name,Values=gpu-reserve-prod-loadtest-*" \
  --query 'SecurityGroups[].GroupId'                                                            # []
aws iam list-roles --query "Roles[?starts_with(RoleName, 'gpu-reserve-prod-loadtest')].RoleName"  # []
aws s3 ls | grep loadtest                                                                       # nothing
```

## The simulated baseline

A t4g.small in standard mode that has spent its credits runs at 20% of each vCPU: 0.4 of a vCPU for the whole instance. Draining a full credit balance first would take hours, so `compose.baseline.yml` puts every container in one systemd slice capped at 40% of a CPU (`twin-setup.sh` creates it). Production sets no per-container CPU limits, so the containers share the cap as they would share a throttled instance. The OS, Docker, and the SSM agent are outside the cap, so the simulation is slightly generous. Results from it are marked as simulated.

## Caveats

- **The worker runs**, as in production. Its cron jobs (expiring pending requests, completing finished bookings, reminders) may change data during a run; the race's correctness check covers the invariants either way.
- **Bookings are not part of the soak**: at a constant rate they would exhaust the seeded quota and free slots and then measure rejections.
- Mail to the seeded domain (`loadtest.example.edu`) is suppressed, so the worker never calls SES; the twin's role could not anyway.
