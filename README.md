# GPU Reserve

A booking platform for shared GPU servers in university research labs. Students reserve GPUs on lab machines within fair-use quotas, admins manage hardware and approve requests for restricted machines, and an assistant answers policy questions and helps find and book hardware with explicit confirmation.

> Work in progress. Setup instructions will be expanded as the project grows.

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
