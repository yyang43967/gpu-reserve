# Architecture

A short overview. It will be expanded as components are built.

## Components

| Component | Technology | Notes |
|---|---|---|
| Frontend | React + TypeScript (Vite), Tailwind, shadcn/ui, TanStack Query | Single-page app; talks to the API under `/api/v1` |
| API | FastAPI (Python 3.12), SQLAlchemy async | Thin routers; business logic in `app/services` |
| Worker | ARQ | Background and scheduled jobs; same image as the API |
| Database | PostgreSQL 16 with `btree_gist` and `pgvector` | Source of truth, including double-booking prevention |
| Cache / queue | Redis 7 | ARQ queue, rate limits, availability cache |
| Email | Mailpit locally | |

## Health

- `GET /api/v1/health` — liveness. The process is up; no I/O.
- `GET /api/v1/ready` — readiness. The database and Redis are reachable; otherwise 503.
