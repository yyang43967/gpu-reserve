# Architecture

An overview of the components and the main request flows. The reasoning behind each choice, with the options considered, is in [decisions.md](decisions.md).

## Components

| Component | Technology | Notes |
|---|---|---|
| Frontend | React + TypeScript (Vite), Tailwind, shadcn/ui, TanStack Query | Single-page app; talks to the API under `/api/v1` |
| API | FastAPI (Python 3.12), SQLAlchemy async | Thin routers; business logic in `app/services` |
| Worker | ARQ | Background and scheduled jobs; same image as the API |
| Database | PostgreSQL 16 with `btree_gist` and `pgvector` | Source of truth, including double-booking prevention |
| Cache / queue | Redis 7 | ARQ queue, rate limits, availability cache |
| Object storage | SeaweedFS locally, S3 in production | Avatars and server images; private bucket, presigned URLs |
| Email | Mailpit locally, SES in production | Sent by the worker from a `notifications` outbox table |
| Assistant | LangGraph with a Postgres checkpointer | Runs inside the API process; streams over SSE |
| Models | Anthropic `claude-haiku-5-5` in production; OpenAI `text-embedding-3-small` for policy search | Provider chosen by `ASSISTANT_MODEL`; vLLM was used only for the model comparison |
| Reverse proxy | Caddy (production) | TLS for the API host, streaming routes unbuffered |

## Health

- `GET /api/v1/health` — liveness. The process is up; no I/O.
- `GET /api/v1/ready` — readiness. The database and Redis are reachable; otherwise 503.

## Authentication

Short-lived access tokens are kept in browser memory. Sessions persist through a rotating refresh token in an `HttpOnly` cookie scoped to `/api/v1/auth`. Design reasoning is in [decisions.md](decisions.md#phase-2--auth-and-users).

### Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/auth/register` | Create a student account and start a session (201) |
| `POST /api/v1/auth/login` | Start a session |
| `POST /api/v1/auth/refresh` | Rotate the refresh cookie and return a new access token |
| `POST /api/v1/auth/logout` | End the current session (204) |
| `GET`, `PATCH /api/v1/me` | Current user; change the display name or avatar |
| `PUT /api/v1/me/password` | Change password; signs out every other session |
| `/api/v1/users` | Admin user management: list, create, read, update, delete |

Session-starting endpoints return `{access_token, token_type, expires_in, user}` and set the `refresh_token` cookie.

### Sign-in, refresh, and reuse detection

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API
    participant R as Redis
    participant D as Postgres

    B->>A: POST /auth/login {email, password}
    A->>R: check per-IP and per-email limits
    A->>D: load user, verify argon2 hash
    A->>D: insert refresh token (new family)
    A-->>B: access token (body) + refresh cookie

    B->>A: GET /me (Authorization: Bearer)
    A->>D: load user by id from token
    A-->>B: 200

    Note over B: access token expires (15 min)
    B->>A: GET /... → 401
    Note over B: take Web Lock "gpu-reserve:refresh"
    B->>A: POST /auth/refresh (cookie)
    A->>D: SELECT token FOR UPDATE
    alt token valid
        A->>D: revoke it, insert successor in same family
        A-->>B: new access token + rotated cookie
        B->>A: retry original request
    else token already revoked (reuse)
        A->>D: revoke whole family, commit
        A-->>B: 401 refresh-token-reused, cookie cleared
        Note over B: redirect to /login?next=…
    end
```

### Session invalidation

| Event | Effect |
|---|---|
| Logout | Revokes the cookie's token family |
| Refresh token reuse | Revokes the whole family |
| Password change | Revokes all of the user's families, then starts a new session for the current device |
| Admin deactivates a user or changes their role | Revokes all of that user's families |
| Deactivation | Access tokens stop working on the next request, because every request reloads the user |

### Errors

Every error response is an RFC 7807 problem document (`application/problem+json`) with a stable `type` URI, `title`, `status`, `detail`, and `instance`. Validation errors add `errors: [{field, message}]`. Rate-limited responses (429) include `Retry-After`.

## Labs and servers

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/labs`, `GET /api/v1/labs/{id}` | List and read labs; students see active labs only |
| `POST`, `PATCH`, `DELETE /api/v1/labs…` | Admin lab management; deleting a lab that still has servers is refused (409) |
| `GET /api/v1/servers`, `GET /api/v1/servers/{id}` | List with filters (`lab_id`, `gpu_model`, `min_gpu_memory_gb`, `min_gpus`, `status`) and read; students don't see retired servers or servers in inactive labs |
| `POST`, `PATCH`, `DELETE /api/v1/servers…` | Admin server management; changing `gpu_count` adds or soft-removes GPU rows |
| `POST /api/v1/uploads/presign` | Signed upload form for an `avatar` (any user) or `server_image` (admins) |

Each server has one GPU row per active GPU, numbered from 0. Reasoning for visibility, GPU numbering, and soft removal is in [decisions.md](decisions.md#phase-3--labs-and-servers).

## Reservations

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/reservations` | Book GPUs on a server; `approved` at once, or `pending` on servers that require approval |
| `GET /api/v1/reservations` | Students see their own; admins see all. Filters: `status`, `server_id`, `user_id` (admins), and `from`/`to` (overlap) |
| `GET /api/v1/reservations/{id}` | Owner or admin |
| `POST /api/v1/reservations/{id}/cancel` | Owner or admin; a running reservation ends early at the next half hour |
| `POST /api/v1/reservations/{id}/approve`, `/reject` | Admin decisions on pending reservations; reject requires a reason |
| `GET /api/v1/servers/{id}/availability` | Free GPUs per 30-minute slot, merged into runs, for up to 14 days |
| `GET /api/v1/me/quota` | GPU-hours used and left in the current quota week, or the week containing `?at=` |
| `GET /api/v1/reservations/{id}.ics` | iCalendar file for one of your own reservations |
| `POST /api/v1/search/slots` | Ranked (server, start, end) candidates for N GPUs for a duration in a window |
| `GET /api/v1/stats/overview` | Dashboard numbers; admins also get queue size and GPU usage |
| `GET /api/v1/stats/utilization` | Admin: booked, pending, and available GPU-hours per server |

Each reservation holds one `gpu_allocations` row per GPU. An exclusion constraint on `(gpu_id, time range) WHERE active` makes double-booking impossible at the database level. Allocation picks the lowest free GPU numbers and retries in a savepoint if another booking takes one first.

### Creating a reservation

```mermaid
sequenceDiagram
    participant A as API
    participant D as Postgres

    A->>A: check request shape (aware times, end after start)
    A->>D: pg_advisory_xact_lock(user)
    A->>D: re-read is_active and quota
    A->>D: SELECT server, lab ... FOR NO KEY UPDATE OF servers
    A->>A: grid, horizon, duration, GPU count, peak hours, server open
    A->>D: count active reservations, load this user's bookings in the quota weeks
    loop first attempt + up to 3 retries, each in a savepoint
        A->>D: free GPUs (removed_at IS NULL, no overlapping active allocation)
        A->>D: INSERT reservation, then allocations in ascending GPU order
        alt exclusion violation (23P01) or deadlock (40P01)
            A->>D: ROLLBACK TO SAVEPOINT, try again
        end
    end
    A->>D: INSERT audit row, COMMIT
```

### Locks

| Path | Takes, in order |
|---|---|
| Booking | user advisory lock, then server row `FOR NO KEY UPDATE` |
| Server change or delete | server row `FOR UPDATE` |
| Quota change, deactivation | user advisory lock (deactivation then locks the user's reservation rows) |
| Approve, reject, cancel | reservation row `FOR UPDATE` |

Bookings on one server take turns on the server row, because two bookings inserting the same GPU at the same moment can each wait for the other's uncommitted row while the exclusion constraint is checked, and deadlock; the load test found this, and inserting GPUs in a fixed order does not prevent it.

Reasoning, alternatives, and the concurrency tests are in [decisions.md](decisions.md#phase-4--reservations).

### Pages

| Page | What it does |
|---|---|
| Server detail (`/servers/:id`) | Seven-day availability timeline in the lab's time zone, and the booking dialog; clicking a free half hour starts a booking there |
| My reservations (`/reservations`) | Weekly quota bar, the signed-in user's reservations with a status filter, and cancel or end early |
| Approvals (`/admin/approvals`) | Admins approve or reject pending requests, soonest start first |

Times are converted between the lab's wall clock and UTC in the browser with `Intl` (`frontend/src/lib/lab-time.ts`). The API only ever sees UTC instants with an offset.

## Uploads

The API never handles image bytes. It signs a form, the browser posts the file to storage, and the API checks the stored object before saving its key.

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API
    participant S as Object storage
    participant D as Postgres

    B->>A: POST /uploads/presign {purpose, content_type, size_bytes}
    A-->>B: {url, fields, key} (key chosen by the server)
    B->>S: POST form (policy: exact key and type, size range)
    S-->>B: 204
    B->>A: PATCH /me {avatar_key} or PATCH /servers/{id} {image_key}
    A->>S: HEAD key (exists, size, type)
    A->>D: save key, commit
    A-->>B: 200 with a presigned GET URL
    A->>S: DELETE the replaced object (after the response)
```

- The bucket is private. Images are read through presigned GET URLs signed at the start of the current hour, so a URL stays the same for an hour and the browser can cache the image.
- In the browser, the storage POST is a plain `fetch` without the access token or cookies. Saving an upload is a separate form action; an upload that is never saved stays in the bucket (a known gap, see decisions).
- Locally, SeaweedFS runs in the `storage` and `full` Compose profiles. The API reaches it as `seaweedfs:8333` on the Compose network and signs browser URLs for `localhost:8333`.
- Upload forms are limited per user (30 per 10 minutes).

## Background jobs and email

The worker runs the ARQ image with a cron schedule. Each task opens a transaction and calls a function in `app/services/jobs.py` with the current time.

| Job | Every | Does |
|---|---|---|
| `expire_pending` | minute | Pending reservations past their start become `expired`; GPUs released; owner notified |
| `complete_finished` | 5 minutes | Approved reservations past their end become `completed`; GPUs released |
| `queue_reminders` | 5 minutes | Queues a reminder for approved reservations starting within the hour |
| `dispatch_notifications` | minute | Sends due rows from the outbox, with retries and backoff |
| `reset_and_refresh_demo` | day, 10:00 UTC | Resets the public demo account, then replays the demo bookings around the current date (see [Demo data](#demo-data)) |

```mermaid
sequenceDiagram
    participant A as API
    participant D as Postgres
    participant W as Worker
    participant M as Mail (Mailpit / SES)

    A->>D: approve + insert notification row (one transaction)
    W->>D: due notification rows
    W->>D: re-read the reservation (skip stale reminders)
    W->>M: send
    W->>D: mark sent, or schedule a retry
```

- Notifications are written in the same transaction as the change they report, so a committed change always has one and a rolled-back change never does.
- The sweeping jobs are single idempotent statements and take a per-job advisory lock, so overlapping ticks do not run together.


## End-to-end tests

Playwright drives the production build of the frontend (`vite preview`) against `docker-compose.e2e.yml`, a separate Compose project with Postgres in memory, Redis, the API, the worker, and Mailpit. Global setup migrates and empties that database, creates an admin, and adds a lab with two servers. See `frontend/e2e/README.md`.

## Availability cache

Free GPUs per server and day are cached in Redis, keyed by a per-server generation number.

| Key | Holds | Expiry |
|---|---|---|
| `availability:v1:<server>:generation` | A counter, incremented after every committed change to the server's allocations or GPU rows | None (a counter that restarted could reach a number whose entries are still alive) |
| `availability:v1:<server>:<generation>:<day>` | That day's allocations | `AVAILABILITY_CACHE_TTL_SECONDS` (300 s) |

- Services call `mark_changed(session, server_id)` when they change allocations; an `after_commit` listener increments the marked servers' generations. A rolled-back transaction increments nothing.
- A reader reads the generation first, then the day entries, loads missing days from Postgres, and stores them under the generation it read. Entries under an old generation are never read again and expire.
- Redis runs with `noeviction`, because the ARQ queue shares it, so every day entry has a TTL.

Reasoning and the load-test results: [decisions.md](decisions.md#availability-caching-and-invalidation), [loadtest/results.md](../loadtest/results.md).

## Assistant

The assistant runs inside the API process as a LangGraph graph: an agent node that calls the model, and a tool node. Conversations are saved by LangGraph's Postgres checkpointer, so a thread survives restarts and deploys.

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/assistant/threads` | Start a conversation (201) |
| `GET /api/v1/assistant/threads` | The caller's conversations |
| `GET /api/v1/assistant/threads/{id}/messages` | Messages, citations, and a waiting confirmation card, if any |
| `POST /api/v1/assistant/threads/{id}/messages` | Send a message; the answer streams back as server-sent events |
| `POST /api/v1/assistant/threads/{id}/resume` | Confirm or decline a waiting card; the rest of the run streams back |

### Tools

| Tool | Does | Writes |
|---|---|---|
| `search_policies` | Vector search over the knowledge base (`kb_chunks`, pgvector), five sections with scores | No |
| `get_current_time` | Current time, in a lab's time zone if given | No |
| `list_servers`, `find_slots` | Servers with filters; ranked free slots for N GPUs over a window | No |
| `get_my_quota`, `list_my_reservations` | The caller's quota and reservations | No |
| `propose_reservation` | Book GPUs, after confirmation | Yes |
| `cancel_my_reservation` | Cancel or end early one of the caller's reservations, after confirmation | Yes |

Every tool takes the user from the authenticated request, never from the model, and calls the same service functions as the web pages. Write tools are bound one at a time (`parallel_tool_calls=False`).

### Confirmation before every write

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API (graph)
    participant M as Model
    participant D as Postgres

    B->>A: POST /threads/{id}/messages "book 2 GPUs ..."
    A->>M: history + tools
    M-->>A: call propose_reservation
    A->>D: dry run of create_reservation, rolled back
    A->>D: save pending_action, audit row, checkpoint
    A-->>B: SSE confirmation_required {server, gpus, time, status, quota_after}
    Note over B: card: Confirm booking / Don't book
    B->>A: POST /threads/{id}/resume {confirm}
    A->>D: claim pending_action
    A->>D: dry run again, compare with what the user saw
    alt unchanged
        A->>D: create_reservation + assistant_actions row + audit (one transaction)
    else changed or declined
        A->>D: no reservation, outcome recorded
    end
    A->>M: tool result
    M-->>A: answer
    A-->>B: SSE tokens, done
```

- `assistant_actions` is unique on `(thread_id, tool_call_id)` and commits with the reservation, so a resume after a crash between the commit and the checkpoint finds the row and writes nothing twice.
- One run per thread: a lease on `chat_threads` (90 s, renewed every 30 s) turns a second concurrent run into 409 `assistant-busy`. A run is not tied to its HTTP response; if the browser disconnects, the run still finishes and saves its state.
- Sending a new message while a card is waiting declines it.

### Streaming and limits

- Events are `data: {json}` frames: `status`, `tool_start`, `tool_end`, `token`, `citation`, `confirmation_required`, `done`, `error`. Caddy passes the two streaming routes through without buffering or compression.
- Per user: 20 messages a minute and 200 a day (messages and resumes both count). For the whole deployment: 300 messages per UTC day; confirmations are exempt. Messages are at most 2,000 characters.
- The model sees the system prompt and about 6,000 tokens of recent history, trimmed to start at a user message. Each model request times out after 30 seconds, and a whole run after 5 minutes.
- Messages written by a different model are sent without their reasoning blocks, so a thread can continue across a model change.

### Tracing

Langfuse traces runs when its keys are set: in evaluations and local development. Production has no tracing configured; the application logs leave out model calls and message content by design.

Reasoning: [decisions.md, Phase 7](decisions.md#phase-7--agent), [model choice](decisions.md#production-model-chosen-from-the-eval). Evaluation: [evals/agent/results/2026-11-10.md](../evals/agent/results/2026-11-10.md).

## Production deployment

```mermaid
flowchart TB
    subgraph gh["GitHub"]
        ci["CI workflow"] -->|"success on main"| deploy["Deploy workflow (production environment)"]
    end
    deploy -->|"OIDC token"| sts["AWS STS: assume deploy role"]
    deploy -->|"push api:SHA"| ecr["ECR"]
    deploy -->|"SSM Run Command: deploy SHA"| host

    subgraph host["EC2 t4g.small (us-east-2)"]
        caddy["Caddy 96 MiB"] --> api["API 448 MiB"]
        worker["Worker 256 MiB"]
        pg[("Postgres 512 MiB")]
        redis[("Redis 96 MiB")]
        timer["systemd backup timer"]
    end

    host -->|"pull"| ecr
    host -->|"env file"| ssm["Parameter Store"]
    timer --> s3b[("S3 backups")]
```

1. The deploy workflow runs only after CI succeeds on `main`, and checks out the commit CI tested.
2. It assumes the deploy role with GitHub's OIDC token. The trust policy accepts only the `production` environment of this repository, matched by its numeric owner and repository IDs, and only `main` may deploy to that environment.
3. It builds the image for `linux/arm64`, pushes it tagged with the full commit SHA (tags are immutable), and runs `release.sh deploy <sha>`.
4. On the instance, `deploy.sh` extracts the deploy files from that image, writes the env file from Parameter Store, backs up the database, runs migrations in a one-off container, starts the stack, and waits for every health check.
5. `release.sh` checks the API's readiness from outside.

Rollback is a deploy of an earlier SHA. Migrations are expand-then-contract, so the previous release runs against the newer schema. The frontend deploys separately on Vercel from `main`. Commands and parameters: [infra/deploy/README.md](../infra/deploy/README.md); infrastructure: [infra/terraform/README.md](../infra/terraform/README.md).

## Backups and the restore check

| When | What |
|---|---|
| Daily, 09:30 UTC (up to 15 minutes later) | `pg_dump` in the Postgres container, checked with `pg_restore --list`, uploaded to the backups bucket; an email through SES if it fails |
| Before every deploy's migrations | The same, tagged with the release |
| Before every restore | A safety dump of the current database |
| Monthly, by hand | `release.sh verify-backup`: the newest dump restored into a throwaway container on the instance and compared with production |

The restore check compares the migration version, extensions, the exclusion constraint, invalid indexes, and every table's row count, and checks that the restored constraint still rejects an overlapping allocation. It never touches the production database. Dumps expire after 14 days. Deploys, backups, and restores share one lock. Details: [decisions.md](decisions.md#backups).

## Demo data

The public demo account is a seeded student. Every day at 10:00 UTC one worker job runs two transactions:

1. **Reset:** deletes the bookings and conversations visitors made with the demo account and clears its rate-limit counters.
2. **Refresh:** deletes every reservation flagged `seeded` and books the demo plan again around the current date, through the same booking service as the API, as the seeded owners. An entry that conflicts with real data is skipped. Rows that are not seeded are never written.

Seeded decisions belong to a seeded admin that nobody can sign in as. Details: [decisions.md](decisions.md#demo-data-kept-current).
