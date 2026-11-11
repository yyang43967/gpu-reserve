# GPU Reserve

A booking platform for shared GPU servers in university research labs. Students reserve GPUs on lab machines within fair-use quotas, admins manage hardware and approve requests for restricted machines, and an assistant answers policy questions and helps find and book hardware with explicit confirmation.

> Work in progress. Setup instructions will be expanded as the project grows.

## Local development

Requirements: Docker (with Compose v2), [uv](https://docs.astral.sh/uv/), Node 24, and pnpm.

```bash
cp .env.example .env
docker compose --profile core up -d --wait   # postgres, redis, api on :8000
curl localhost:8000/api/v1/ready
```

The `full` profile adds the background worker, Mailpit (UI on :8025), object storage (S3 API on :8333), and the frontend dev server on :5173:

```bash
docker compose --profile full up -d --wait
```

For day-to-day work, the lighter setup is `core` in Docker plus the frontend and tests on the host. The Vite dev server proxies `/api` to `localhost:8000`:

```bash
cd frontend && pnpm install && pnpm dev
cd backend && uv sync && uv run pytest
```

### Running the API with hot reload

`docker-compose.override.yml` is loaded automatically whenever you run `docker compose` from the repository root without `-f`. It bind-mounts `backend/app` into the API and worker containers (read-only) and starts the API with `uvicorn --reload`, so changes under `backend/app` take effect without a restart:

```bash
docker compose --profile core up -d --wait
```

Check that the running container picked it up. The output should list the `backend/app` mount and a command ending in `--reload`:

```bash
docker inspect gpu-reserve-api-1 --format '{{range .Mounts}}{{.Source}} {{end}}{{.Config.Cmd}}'
```

If it doesn't, the container was created from the base file alone (for example with `-f docker-compose.yml`). Running the `up` command above again recreates it with the override.

Only `backend/app` is mounted. Rebuild the image after changing anything else that is copied into it: dependencies (`pyproject.toml`, `uv.lock`), the `Dockerfile`, or migrations under `backend/alembic`:

```bash
docker compose up -d --build api
```

The worker uses the same image, so recreate it too when it is running (`docker compose --profile full up -d --wait`).

To run the built images exactly as CI does, without the override, pass the base file explicitly:

```bash
docker compose -f docker-compose.yml --profile core up -d --wait
```

### Uploads and object storage

Avatars and server images are uploaded by the browser directly to S3-compatible storage. Locally that is SeaweedFS, in the `storage` profile (also part of `full`). Start it next to `core` when working on uploads:

```bash
docker compose --profile core --profile storage up -d --wait
```

It creates the `gpu-reserve-uploads` bucket on startup, allows CORS from `CORS_ORIGINS`, and listens on `127.0.0.1:8333`. Without it, everything except uploads and image URLs works. When the API runs on the host instead of in Compose, set both `S3_ENDPOINT_URL` and `S3_PUBLIC_ENDPOINT_URL` to `http://localhost:8333` (see `.env.example`).

### Database migrations and the first admin

Apply migrations, then create an admin account. The password is read from `ADMIN_PASSWORD` or prompted for. If the email already exists, that account is made an active admin and its password is reset.

```bash
cd backend
uv run alembic upgrade head
uv run python -m app.cli create-admin --email admin@example.edu --name "Lab Admin"
```

Against the Compose stack instead (rebuild the image first if migrations changed, since `backend/alembic` is not mounted):

```bash
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m app.cli create-admin --email admin@example.edu --name "Lab Admin"
```

Admins manage accounts, labs, and servers under `/admin` in the web app.

### Assistant evaluation

The agent evaluation runs every case in `evals/agent/cases.jsonl` (readable as `evals/agent/cases.md`) against the model in `ASSISTANT_MODEL`. Each case gets its own copy of a template database (migrated, seeded, and holding the knowledge base), with the clock fixed so relative dates mean the same day in every run. The database role in `DATABASE_URL` must be allowed to create databases; the Compose Postgres user is.

```bash
cd backend
uv run python -m app.agent.evaluation validate        # replay every case's expectations through the services
uv run python -m app.agent.evaluation describe        # regenerate evals/agent/cases.md
uv run python -m app.agent.evaluation run --repeat 2  # two runs of ASSISTANT_MODEL
uv run python -m app.agent.evaluation judge ../evals/agent/runs/<run>...      # two judges on the judged answers
uv run python -m app.agent.evaluation label-sheet ../evals/agent/runs/<run>... # answers to label by hand, blind
uv run python -m app.agent.evaluation label-md     # the label sheet as evals/agent/hand_labels.md
uv run python -m app.agent.evaluation label-read   # verdicts from the .md back into hand_labels.jsonl
uv run python -m app.agent.evaluation compare ../evals/agent/runs/<run>...    # the comparison report
```

`run` needs the model's API key (or `VLLM_BASE_URL` and `VLLM_API_KEY`), and `OPENAI_API_KEY` for policy-search queries that are not yet in `evals/agent/query_embeddings.json.gz`. It refuses a working tree with uncommitted changes unless given `--allow-dirty`, which the run records. The query embedding cache is the one exception: runs add to it, each run records its hash, and it is committed after the last run of a series. Each run writes `evals/agent/runs/<run id>/run.json` (the full configuration) and `cases.jsonl.gz` (one row per case). `judge` needs both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` and adds `judgments.jsonl.gz` to each run; `compare` reads the runs, verdicts, and hand labels and writes `evals/agent/results/<date>.md`.

## Results

The assistant was evaluated on 63 cases (bookings, rule violations, conflicts, cancellations, clarifying questions, slot search, policy and hardware questions, out-of-scope requests, other users' data, prompt injection, and changes of mind), two runs per configuration from one frozen commit. A case passes a configuration only if it passed both runs (strict). Detail: [evals/agent/results/2026-11-10.md](evals/agent/results/2026-11-10.md).

| Configuration | Strict | Runs 1, 2 | Wrong cards (runs 1, 2) | Turn p50 / p95 | Cost per run |
|---|---|---|---|---|---|
| claude-haiku-5-5, effort low | **58/63 (92%)** | 59, 61 | 0, 0 | 2.7 / 8.1 s | $0.05–0.07 |
| gpt-5.4-mini, effort low | 50/63 (79%) | 55, 54 | 0, 2 | 2.9 / 7.4 s | $0.24–0.25 |
| Qwen3.6-35B-A3B, thinking on (vLLM, L40S) | 48/63 (76%) | 54, 53 | 1, 0 | 9.4 / 36.0 s | $0.16–0.18 GPU |
| Qwen3.6-35B-A3B, thinking off (vLLM, L40S) | 23/63 (37%) | 33, 28 | 1, 5 | 3.0 / 10.0 s | $0.06 GPU |

- **Pairwise** (exact McNemar on strict outcomes, Holm-corrected over 6 pairs): Haiku beats gpt-5.4-mini (9 vs 1 disagreeing cases, Holm p = 0.043) and Qwen thinking on (12 vs 2, p = 0.039); Qwen thinking off is worse than every other configuration (p < 0.001); Qwen thinking on vs gpt-5.4-mini shows no significant difference (8 vs 10, p = 0.815). Every conclusion is the same when the second judge decides the judged cases.
- **Safety:** 0 safety failures on injection and other users' data in every run. Wrong cards (a confirmation card that did not match the request, or one nobody asked for) are in the table; every such card was answered as planned or cancelled, and the database diff shows what it wrote.
- **Cost of the whole final evaluation:** OpenAI $0.49, Anthropic $0.12, Modal GPU about $0.60 for one 18.5-minute deploy (including a 259 s cold start), judging $0.27.

### Limitations

- **The checkers were revised against the gpt-5.4-mini and Haiku pilot answers.** Mention lists, question phrasing, and two rubric points were fixed after reading their pilot transcripts, so for those two models the checks are partly in-sample; for Qwen they are out-of-sample.
- **The argument normalization fired only for Qwen.** Optional arguments sent as "None", "null", or "" were treated as not set 66 and 52 times (thinking on) and 14 and 6 times (thinking off), and never for the hosted models. Qwen's scores therefore include that repair; in the pilot before it, 29 of 63 thinking-on cases ran out of steps.
- **Hand labels cover hosted answers only.** The primary judge agreed with blind hand labels on 55 of 60 verdicts, all on gpt-5.4-mini and Haiku answers; no Qwen answer was labeled.
- **Low reasoning effort only** for the hosted models; Qwen was run with thinking on and off.
- **Small sample.** With 63 cases, only disagreeing cases carry information, and the strictest Holm threshold needs at least an 8-0 split. The Haiku-vs-gpt-5.4-mini result (Holm p = 0.043) sits just under the line and is fragile; it also partly reflects consistency, since under the strict rule a case that passes one run and fails the other counts as failed, and gpt-5.4-mini's two runs disagreed on 9 cases against Haiku's 4.

### Performance

GPU availability is cached in Redis per server and day, and a write invalidates it by starting a new generation for that server when it commits. A k6 load test on a local stack limited to the size of a t4g.small (2 vCPUs, 2 GiB, burst capacity) measured the effect. Three repeats per configuration, medians. Detail: [loadtest/results.md](loadtest/results.md).

**The cache cuts database load, not latency or throughput, on one API process.** Capacity is bound by the API's CPU: at saturation it sat at its limit while Postgres used 3–9% of a core for slot search. Slot search peaked at 41–44 req/s in all 12 capacity runs, with the cache off or on.

| Cache off → on | Postgres CPU at a fixed rate | p50 | p95 | Peak throughput |
|---|---|---|---|---|
| Availability, 161 req/s | 29% → 8% (−73%) | 4.5 → 3.8 ms | 210 → 160 ms | 248 → 277 req/s |
| Slot search, 5.9 req/s | 6% → 3% (−39%) | 40 → 38 ms | 123 → 120 ms | 44 → 43 req/s |

- **Latency at these rates is unchanged** within the spread between repeats.
- **Slot search's sustained rate shows no consistent difference** across two capacity runs.
- **The cache matters when the API scales out.** More uvicorn workers or instances would load Postgres harder, and uncached availability already took 36% of Postgres's core at saturation against 8% cached. The cache keeps the database from becoming the shared bottleneck.
- **The booking deadlock.** The load test also found that concurrent bookings on one server could deadlock on the exclusion constraint. Bookings on a server are now serialized with a row lock. In every race since, 100 students booking 2 GPUs on an 8-GPU server produced exactly 4 bookings and 96 refusals.

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI application, workers, tests |
| `frontend/` | React + TypeScript single-page app |
| `kb/` | Knowledge base documents for the assistant |
| `evals/` | Retrieval and agent evaluation data and results |
| `loadtest/` | k6 scripts and results |
| `serving/` | Self-hosted model deployment |
| `infra/` | Terraform and production deployment files |
| `docs/` | Architecture and design decisions |
