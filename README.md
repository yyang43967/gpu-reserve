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

The `full` profile adds the background worker, Mailpit (UI on :8025), and the frontend dev server on :5173:

```bash
docker compose --profile full up -d --wait
```

`docker-compose.override.yml` is applied automatically and enables hot reload with source bind mounts. To run the built images exactly as CI does, pass `-f docker-compose.yml`.

For day-to-day work, the lighter setup is `core` in Docker plus the frontend and tests on the host:

```bash
cd frontend && pnpm install && pnpm dev
cd backend && uv sync && uv run pytest
```

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI application, workers, tests |
| `frontend/` | React + TypeScript single-page app |
| `kb/` | Knowledge base documents for the assistant |
| `evals/` | Assistant evaluation dataset, runner, results |
| `loadtest/` | k6 scripts and results |
| `serving/` | Self-hosted model deployment |
| `infra/` | Terraform and production deployment files |
| `docs/` | Architecture and design decisions |
