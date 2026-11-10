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
uv run python -m app.agent.evaluation compare ../evals/agent/runs/<run>...    # the comparison report
```

`run` needs the model's API key (or `VLLM_BASE_URL` and `VLLM_API_KEY`), and `OPENAI_API_KEY` for policy-search queries that are not yet in `evals/agent/query_embeddings.json.gz`. It refuses a working tree with uncommitted changes unless given `--allow-dirty`, which the run records. The query embedding cache is the one exception: runs add to it, each run records its hash, and it is committed after the last run of a series. Each run writes `evals/agent/runs/<run id>/run.json` (the full configuration) and `cases.jsonl.gz` (one row per case). `judge` needs both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` and adds `judgments.jsonl.gz` to each run; `compare` reads the runs, verdicts, and hand labels and writes `evals/agent/results/<date>.md`.

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
