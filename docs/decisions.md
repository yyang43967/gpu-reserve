# Design decisions

Each entry records the context, the decision, and why. Newest phases at the bottom.

## Phase 1 — Scaffold

### Compose profiles and memory limits
- **Context:** Development happens on an 8 GB Apple Silicon laptop. Running every service all the time wastes memory.
- **Decision:** Two profiles. `core` runs postgres, redis, and api. `full` adds the worker, Mailpit, and the frontend dev server. Every container has a `mem_limit` (postgres 512m, redis 128m, api 384m, worker 256m, mailpit 64m, frontend 768m). Ports bind to `127.0.0.1` only.
- **Why:** `core` uses about 115 MiB in practice and covers most backend work. The frontend limit is higher because the Vite dev server peaked around 440 MiB at startup. Day-to-day, the frontend and tests run on the host.

### Hot reload only in an override file
- **Context:** Local development wants code reload. CI and production should run the built image exactly as shipped.
- **Decision:** `docker-compose.yml` runs the built images with no reload. `docker-compose.override.yml`, which Compose loads automatically, adds `--reload` and source bind mounts. CI uses `-f docker-compose.yml` to skip it.
- **Why:** The smoke test then exercises the same command and image that production runs.

### Local S3 deferred to Phase 3
- **Context:** MinIO no longer publishes community Docker images. `minio/minio` returns 404 and pinned older tags cannot be pulled.
- **Decision:** No object storage service in Compose yet. Choose an S3-compatible replacement when presigned uploads are built.
- **Why:** Nothing uses object storage before then. Picking a replacement now would add an untested service and a dependency with no consumer.
- **Outcome:** SeaweedFS, chosen in Phase 3. See "Local object storage: SeaweedFS".

### `pgvector/pgvector:pg16` as the Postgres image
- **Context:** The schema needs `btree_gist` (contrib) and `pgvector`.
- **Decision:** Use the official pgvector image for Compose and tests.
- **Why:** It is the stock Postgres 16 image with pgvector added, publishes arm64, and keeps tests on the same engine and extensions as runtime.

### Readiness endpoint added now
- **Context:** The scaffold only needs a liveness check, but Compose and CI need to know that the API can actually reach its dependencies.
- **Decision:** `GET /api/v1/health` does no I/O. `GET /api/v1/ready` runs `SELECT 1` and a Redis `PING` concurrently, each with a 2-second timeout, and returns 503 if either fails. The response is a small status body listing each check, not a problem-details error.
- **Why:** It proves the DB and Redis wiring end to end from the first commit, and the container healthcheck can use it. A probe response should not depend on the error-handling layer, so it stays a plain status document.

### One backend image for api and worker
- **Decision:** The worker runs the api image with a different command (`arq app.workers.settings.WorkerSettings`). Its healthcheck is `arq --check`, backed by a 30-second heartbeat.
- **Why:** Shared code and dependencies, one build, and no drift between the two.

### Alembic deferred to Phase 2
- **Decision:** Set up Alembic together with the first models instead of committing an empty migration environment.
- **Why:** The first migration (extensions plus users and auth tables) is easier to review alongside the models that need it.

### React 19 instead of React 18
- **Context:** The current shadcn/ui components and Tailwind v4 setup target React 19.
- **Decision:** React 19.3 with React Router 7.
- **Why:** It avoids fighting component templates and type definitions written for 19. Nothing planned depends on React 18 behavior.

### Tailwind v4 and shadcn/ui with a local `cn` helper
- **Decision:** Tailwind v4 through `@tailwindcss/vite`, with no `tailwind.config.js`. shadcn/ui components are copied into `src/components/ui`. They import `cn` from `src/lib/utils.ts` (clsx + tailwind-merge).
- **Why:** The newest shadcn CLI imports a separate `cn` npm package. Keeping the helper local avoids an extra runtime dependency for a five-line function.

### TypeScript 6.0 and ESLint 9
- **Context:** typescript-eslint supports TypeScript only up to 6.0, and `eslint-plugin-jsx-a11y` does not yet declare support for ESLint 10.
- **Decision:** Pin `typescript@~6.0` and `eslint@9`.
- **Why:** A clean peer-dependency tree is worth more than the newest major. Revisit when the plugins catch up.

### Node 24 LTS and a pinned pnpm
- **Decision:** Node 24 via `.nvmrc` and `engines`. pnpm pinned through `packageManager` (`pnpm@12.16.1`), which corepack, CI, and the lockfile all honor.
- **Why:** One toolchain version everywhere, on an LTS release line.

### CI runner split
- **Context:** The repository is private for now, so Actions minutes are billed. GitHub offers native arm64 runners (`ubuntu-24.04-arm`) to private repositories.
- **Decision:** Pull requests run lint, type checks, and unit and integration tests on `ubuntu-latest`. Pushes to `main` also build the arm64 image and smoke-test the `core` stack on `ubuntu-24.04-arm`. Playwright will join the `main`-only job in Phase 5.
- **Why:** Fast, cheap PR feedback, plus a native build of the production architecture without QEMU. The runner labels do not change when the repository goes public.

### Integration tests with testcontainers
- **Decision:** Tests marked `integration` start real Postgres and Redis containers once per session. They run on every PR. Locally, `pytest -m "not integration"` skips them when Docker is off.
- **Why:** Readiness, and later the concurrency guarantees, only mean something against the real database.

## Phase 2 — Auth and users

### Refresh token rotation and reuse detection
- **Context:** Access tokens are short-lived JWTs. Sessions last longer through a refresh token in a cookie. A stolen refresh token must not give an attacker an indefinite session.
- **Options considered:**
  1. No rotation: one long-lived refresh token per session. Simple, but a stolen token works until it expires and theft is never detected.
  2. Rotation with a server-side grace window: a recently rotated token presented again within a few seconds is treated as a harmless duplicate (two tabs refreshing at once) instead of reuse.
  3. Strict rotation with reuse detection, and the client serializing refreshes (chosen).
- **Decision:** Every refresh revokes the presented token and issues a new one in the same *family* (one family per sign-in). Presenting any token that is already revoked, at any age, revokes the whole family, returns 401 `refresh-token-reused`, clears the cookie, and logs a warning with the user and family id. There is no grace window.
- **How the race is handled:**
  - Server: the refresh looks the token up with `SELECT … FOR UPDATE`, so two concurrent refreshes of the same token are serialized. Exactly one rotates; the other sees a revoked token and counts as reuse. A test asserts this, and removing the row lock makes it fail.
  - The family revocation is committed before the 401 is raised, so the request's rollback cannot undo it.
  - Client: within a tab, concurrent 401s share one refresh promise. Across tabs, the refresh request runs inside `navigator.locks.request("gpu-reserve:refresh")`. The browser attaches the cookie when the request is sent, which only happens once the lock is held, so every tab presents the newest token. Without the Web Locks API, the client falls back to in-tab serialization only.
- **Why the grace window was rejected:** it cannot tell a second tab from an attacker. If an attacker with a stolen token rotates first, the victim's next refresh arrives with the now-revoked token inside the window and is treated as a superseded duplicate. The family survives and the attacker keeps a valid session. Strict detection turns that same event into a revocation of both sessions.
- **Cost:** a lost refresh response (the server rotated, then the network dropped the reply) leaves the browser with a revoked cookie. The next refresh counts as reuse and the user has to sign in again. That is rare and fails safe.
- **Other session rules:**
  - Changing the password revokes every family of that user, then starts a new session for the current device.
  - An admin deactivating a user or changing their role revokes all of that user's families.
  - Logout revokes the family of the presented cookie when it belongs to the signed-in user, and is idempotent.
  - Refresh tokens are 32 random bytes, stored as a SHA-256 hash. They have enough entropy that a slow hash would add nothing.

### Token storage in the browser
- **Options considered:** both tokens in `localStorage`; both in cookies; access token in memory and refresh token in an `HttpOnly` cookie (chosen).
- **Decision:** The access token (HS256, 15 minutes) lives only in a module variable. The refresh token lives in an `HttpOnly` cookie. A page load gets a fresh access token from the cookie.
- **Why:**
  - Anything in `localStorage` can be read by any script that runs on the page, so one XSS bug would leak long-lived credentials.
  - An `HttpOnly` cookie cannot be read by scripts. An injected script running on the page can still call the refresh endpoint (the browser attaches the cookie) and send itself the short-lived access tokens it gets back, for as long as the page stays open. It can never read the long-lived refresh token itself, so the attack ends when the page closes and the stolen access tokens expire within 15 minutes.
  - Keeping the access token out of cookies means ordinary API requests carry it in the `Authorization` header and are not exposed to CSRF.
- **Signing key:** `JWT_SECRET` has no default in the settings class and must be at least 32 bytes in every environment. The development value lives only in the Compose file and the test configuration, so a deployment that forgets to set it fails at startup instead of signing tokens with a published key.

### Refresh cookie path, SameSite, and CSRF
- **Decision:** The cookie is `refresh_token`, `HttpOnly`, `SameSite=Lax`, `Path=/api/v1/auth`, `Max-Age` of 7 days, with `Secure` on except in plain-HTTP local setups (`REFRESH_COOKIE_SECURE=false`).
- **Why:**
  - The narrow path means the browser sends the cookie only to the auth endpoints, not with every API call.
  - The cookie can only do two things: refresh (which returns a token in the response body, unreadable cross-origin) and logout. `SameSite=Lax` keeps it off cross-site POSTs, which covers both. A separate CSRF token would protect nothing further.

### Loading the user on every request
- **Options considered:** trust role and active status from JWT claims; load the user from the database on each request (chosen).
- **Decision:** The access token carries only `sub`, `iat`, `exp`, and `typ=access`. `get_current_user` loads the user by primary key on every request.
- **Why:** One indexed lookup per request is cheap at this scale. In exchange, deactivation and role changes apply on the next request, not up to 15 minutes later when the token expires.

### Password hashing: argon2id over bcrypt
- **Decision:** argon2id with the OWASP baseline profile (19 MiB memory, 2 iterations, parallelism 1). Outdated hashes are upgraded on the next successful login.
- **Why:**
  - argon2id is memory-hard, so GPU and ASIC cracking is far more expensive than for bcrypt.
  - It has no 72-byte input limit, so long passphrases are not silently truncated.
  - Hashing runs in a worker thread behind a semaphore of 4. That caps hashing memory at about 76 MiB and keeps the event loop responsive.

### Login, registration, and password-change rate limiting
- **Context:** argon2 is deliberately expensive. Unthrottled login is both a brute-force risk to accounts and a CPU and memory exhaustion risk to the server. Registration also hashes a password.
- **Decision:** Fixed-window counters in Redis (`INCR`, then `EXPIRE … NX` in one pipeline). Throttled requests get 429 problem responses with `Retry-After` set to the key's remaining TTL.

  | Key | Counts | Limit |
  |---|---|---|
  | `login:ip:{ip}` | every login attempt | 60 per minute |
  | `login:email:{sha256(email)}` | failed logins only; cleared on success | 5 per 15 minutes |
  | `register:ip:{ip}` | every registration | 20 per hour |
  | `password-change:user:{id}` | wrong current passwords; cleared on success | 5 per 15 minutes |

- **Details and reasoning:**
  - **Checked before hashing.** Every limit is checked before argon2 runs, so a throttled request costs no hashing CPU.
  - **Per IP vs per email.** The per-IP key bounds the load one client can generate. The per-email failure key stops a distributed attack on a single account, even when the attempts come from many IPs.
  - **Failures only, per email.** A user who signs in successfully is never throttled by their own earlier typos once they get it right.
  - **Per-IP login limit of 60/min, not lower.** On a campus a whole lab often shares one NAT address. Brute force is already covered by the per-email failure limit, and the hashing semaphore protects CPU and memory, so the per-IP limit only has to stop gross abuse.
  - **No raw emails in Redis.** Email keys hold a SHA-256 of the normalized address.
  - **Password change is throttled per user id.** A stolen 15-minute access token could otherwise be used to brute-force the current password at full speed and turn temporary access into a permanent takeover. The hashing semaphore protects the server, not the account.
  - **Fail open.** Every Redis call has a 0.5-second timeout. A timeout or connection error is logged and the request proceeds unthrottled. Login keeps working during a Redis outage, which `/ready` already reports. The trade-off is accepted: an outage temporarily removes throttling but not authentication.
  - **Client IP behind the proxy.** Uvicorn runs with `--proxy-headers`. In production, `FORWARDED_ALLOW_IPS` must be set to the reverse proxy's address so that only the proxy's `X-Forwarded-For` is trusted and the per-IP key is the real client. To be set during deployment.
- **Known weaknesses and upgrade paths:**
  - **Lockout by a third party.** Because the per-email counter ignores the source, anyone can lock a user out for 15 minutes by deliberately failing logins with their email. Future options: key the failure counter on email plus IP, so an attacker only locks out their own address; or replace the hard block with a CAPTCHA step once the limit is reached.
  - **Fixed-window boundary burst.** A client can send up to twice the limit around a window boundary (the end of one window plus the start of the next). The upgrade path is a sliding window (a sorted set of timestamps, or a weighted count over the current and previous windows) or a token bucket.

### Email normalization
- **Decision:** `normalize_email()` in the users service trims and lowercases addresses. Every path that touches an email calls it: register, login, admin create, and the `create-admin` command. The column has a plain unique constraint.
- **Why:** Normalizing in the service layer covers callers that never pass through the HTTP schemas, such as the CLI. It avoids the `citext` extension. A concurrent duplicate registration is still caught by the unique constraint and reported as 409 `email-taken`.

### Login responses do not reveal accounts
- **Decision:** An unknown email, a wrong password, and an inactive account all return the same 401 `invalid-credentials`. For an unknown email the service still verifies against a dummy hash, and the active check happens only after the password is verified.
- **Why:** Equal responses and similar timing keep the login form from being used to discover which emails have accounts, or which accounts are disabled.
- **Known gap: registration.** Registering an email that already has an account returns 409 `email-taken`, so the registration form does reveal whether an email exists. This is an accepted usability trade-off: the user learns immediately to sign in instead. Enumeration through it is slowed by the per-IP registration limit (20 per hour). The full fix is email verification: registration always answers "check your inbox", and the email sent to an existing user tells them their account already exists.

### Error kinds and RFC 7807 responses
- **Options considered:** raise FastAPI `HTTPException` from services; return result objects; raise domain exceptions mapped in one place (chosen).
- **Decision:** Services raise subclasses of `DomainError`, each with a `kind` (`NOT_FOUND`, `CONFLICT`, `UNAUTHENTICATED`, `FORBIDDEN`, `INVALID`, `RATE_LIMITED`), a stable `type` slug, and a title. One handler maps kinds to 404, 409, 401, 403, 422, and 429 and writes `application/problem+json`. Validation errors become 422 with `errors: [{field, message}]`. Unknown routes and methods also return problem documents. Unhandled exceptions become a generic 500 that logs the traceback and leaks nothing.
- **Why:**
  - Services stay free of HTTP concerns and can be called from the CLI, workers, and later the assistant's tools.
  - The status mapping lives in one table, so the same kind of error can never get two different codes.
  - The `type` slug is a stable contract: the frontend keys field-level messages on it (`email-taken` goes under the email input, `incorrect-password` under the current-password input).
  - 401 responses carry `WWW-Authenticate: Bearer`. 429 responses carry `Retry-After`.
  - The unhandled-error middleware sits inside the CORS middleware, so even a 500 is readable by the browser.

### Admin user management
- **Hard delete vs deactivate:** `DELETE /users/{id}` removes the row, and refresh tokens cascade. Deactivation (`is_active=false`) is the normal way to cut off access while keeping history. Once reservations exist, their foreign key to users will be `RESTRICT`, and deleting a user with reservations will return 409 telling the admin to deactivate instead.
- **Self-protection:** an admin cannot delete, deactivate, or demote their own account. This prevents the last admin from locking everyone out. The API enforces it (409 `cannot-modify-self`), and the UI disables those controls on the admin's own row.
- **`GET /users/{id}`** exists alongside the list, for edit forms and future detail views.
- **Explicit initial password:** admins set the initial password when creating an account. There is no generated default and no email invite yet.
- **Bootstrap:** `python -m app.cli create-admin` creates the first admin. If the email already exists, the account is promoted to an active admin and its password reset, so the command can also recover a locked-out installation.

### CORS for credentialed requests from the frontend origin
- **Context:** In production the frontend is served from Vercel at `app.<domain>` and the API from `api.<domain>`. The refresh cookie must reach the API from browser code.
- **Decision:**
  - `CORS_ORIGINS` lists the exact origin, `https://app.<domain>`. Browsers reject a wildcard origin on credentialed responses, and reflecting any origin would let any site make credentialed calls.
  - `allow_credentials=True`, and the frontend sends `credentials: "include"`.
  - Methods and headers are listed explicitly, the same in every environment: `GET`, `POST`, `PATCH`, `PUT`, `DELETE`, and `OPTIONS`, and the `Authorization` and `Content-Type` headers. CORS-safelisted headers such as `Accept` are always allowed. A preflight asking for any other method or header gets a 400, so the browser never sends the request. Tests cover the allowed set and the rejections.
  - Why explicit lists instead of `*`: for `allow_headers=["*"]`, Starlette echoes back whatever headers the preflight asks for, and `allow_methods=["*"]` expands to every method it knows, including `HEAD` and `QUERY`. A page on an allowed origin could then send any custom header or method. The lists document the API's real surface and fail loudly if the frontend starts sending something new.
  - The cookie works because `app.<domain>` and `api.<domain>` are the same site, so `SameSite=Lax` allows it on these requests.
- **Accepted limitation:** Vercel preview deployments on `*.vercel.app` are cross-site to the API. The browser will not send a `SameSite=Lax` cookie there, so previews cannot keep a session. Previews are for checking layout, so this is accepted.

### Frontend session handling
- **Decision:** An `AuthProvider` holds the current user in the TanStack Query cache under `["me"]`. On load it exchanges the refresh cookie for an access token. The API client retries a 401 once after refreshing; if the refresh fails, a session-expired handler clears cached data and the route guards send the user to `/login?next=…`.
- **Sign-out:** the guard, not the button, performs the redirect after sign-out. The query cache notifies observers asynchronously, so navigating right after the logout request could still see the old session and bounce back into the app. A deliberate sign-out goes to a plain `/login`; an expired session keeps `next` so the user returns to the same page.
- **`next` is restricted** to same-site paths (no `//host`, schemes, or backslashes), so a crafted link cannot redirect elsewhere after sign-in.
- **Guards shape navigation only.** Every permission is enforced by the API.

### Native select instead of a custom dropdown
- **Decision:** Form dropdowns use a styled native `<select>`, not a custom popover listbox.
- **Why:** It keeps the platform picker on phones, is accessible by default, works directly with React Hook Form's `register`, and needs no portal or pointer-capture workarounds in tests. The custom component adds nothing the forms need.

### Scope moved to later phases
- **`GET /me/quota` moves to Phase 4.** Quota usage needs reservations, and a stub returning zero would be misleading.
- **Avatar upload moves to Phase 3.** The `avatar_key` column exists now, but `PATCH /me` accepts only `full_name` until presigned uploads are built. Done in Phase 3; see "Uploads".

### Real migrations in tests
- **Decision:** Integration tests run `alembic upgrade head` once per session against the test container, then truncate tables between tests. A dedicated test upgrades to head, downgrades to base, and checks that autogenerate finds no drift between the models and the migrations.
- **Why:** `create_all` would hide missing or wrong migrations, including extensions, enum types, and constraint names. Running the real chain proves that what ships is what the tests exercised. CI also applies the migrations inside the built image before the smoke test.

## Phase 3 — Labs and servers

### Local object storage: SeaweedFS
- **Context:** Uploads use presigned URLs, so the browser talks to object storage directly. Development needs an S3-compatible service that runs on arm64, fits the 8 GB laptop, and behaves like S3 on the points the upload design depends on.
- **Options considered:** SeaweedFS (`chrislusf/seaweedfs`) and RustFS (`rustfs/rustfs`). Both publish arm64 images and are Apache-2.0. SeaweedFS has about ten years of releases. RustFS reached 1.0 recently and ships point releases weekly.
- **How they were compared:** one script, run against `chrislusf/seaweedfs:4.51` and `rustfs/rustfs:1.0.4`, each in a container limited to 256 MiB, signing with boto3:

  | Check | SeaweedFS | RustFS |
  |---|---|---|
  | Unsigned GET, PUT, bucket list, and service list rejected | 403 | 403 |
  | Presigned POST under the size limit (2 MiB and 5 MiB) | 204 | 204 |
  | Presigned POST over the limit | 400, nothing stored | 400, nothing stored |
  | Presigned POST with a content type other than the signed one | 403 | 400 |
  | Presigned POST with an empty file (`content-length-range` starts at 1) | 400 | 400 |
  | Presigned PUT with a body length or content type other than the signed one | 403 | 403 |
  | HEAD reports length and type; DELETE removes the object | yes | yes |
  | CORS preflight from the frontend origin allowed, other origins refused | yes | yes |
  | URL signed for one host rejected under another | 403 | 403 |
  | Bucket-scoped identity cannot create buckets | denied | not tested (see below) |
  | Memory, idle → after the run | 60 → 65 MiB | 70 → 102 MiB |

- **Decision:** SeaweedFS 4.51, running `weed mini` (master, volume, filer, and S3 gateway in one process) in the `storage` and `full` Compose profiles, with `mem_limit: 256m` and `GOMEMLIMIT=160MiB`. WebDAV, the admin UI, and the Iceberg and Lance endpoints are turned off.
- **Why:**
  - Both servers passed every upload check, so the decision came down to setup and footprint.
  - SeaweedFS reads its identities from a file. The only identity is the application's, with read, write, list, and tagging on the uploads bucket. There is no admin identity and no anonymous identity, so unsigned requests get 403. The server creates the bucket itself at startup (`-bucket`) and answers CORS for the configured origins (`-s3.allowedOrigins`). No admin key exists anywhere in the local stack, not even for setup.
  - RustFS starts with a root key. A bucket-scoped user would need a separate setup step through its admin API using that root key, so the RustFS run above used the root key and does not show a scoped identity. That setup step and its key are what SeaweedFS avoids.
  - SeaweedFS used less memory in the same test.
  - 4.51 is pinned rather than 4.52, which had been out only a few days.
- **Configuration details:**
  - `infra/local/seaweedfs/start.sh` writes the identity file from `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and `S3_BUCKET_UPLOADS` (mode 600, owned by the service user), then hands over to the image's entrypoint, which drops root. Compose supplies development-only defaults, as it does for `JWT_SECRET`.
  - The allowed CORS origins follow `CORS_ORIGINS`, the same list the API uses.
  - Only the S3 port (8333) is published, on `127.0.0.1`. Data and filer metadata share the `seaweedfs-data` volume.
- **Local only:** this setup is for development. Production uses AWS S3, with bucket CORS for the frontend origin and an IAM policy on the instance role scoped to the uploads bucket. Both are set in Terraform.

### Lab names are unique regardless of case
- **Decision:** A unique index on `lower(name)`. The stored name keeps the case the admin typed. The service checks first for a clear 409 `lab-name-taken`, and maps a violation of the index to the same error when two requests race past the check.
- **Why:** "Vision Lab" and "vision lab" are the same lab to a person. A plain unique constraint would allow both. The `citext` extension would also work, but a single expression index is enough and keeps the column an ordinary `varchar`.

### GPU memory follows from the GPU model
- **Decision:** `servers.gpu_memory_gb` is stored, but set by the service from a fixed table keyed by `GpuModel`. The API does not accept it, and changing the model updates it.
- **Why:** Every supported model has exactly one memory size, so an admin-entered value could only be wrong. Storing it keeps the `min_gpu_memory_gb` filter a plain indexed comparison.

### GPU rows: soft removal and numbering
- **Context:** A server's `gpu_count` can change. GPU rows must follow it, and later phases point reservations' allocations at individual GPU rows.
- **Options considered:**
  1. Delete the rows of removed GPUs. Simple, but rows that past allocations refer to would have to go too (cascade, losing history) or block the change forever (restrict).
  2. Soft removal with a `removed_at` timestamp (chosen).
- **Decision:**
  - GPUs are numbered 0 to `gpu_count - 1`. A partial unique index on `(server_id, index) WHERE removed_at IS NULL` allows one active GPU per number, and a plain index on `server_id` covers removed rows for cascades and history lookups.
  - Lowering the count sets `removed_at` on the highest numbers. Low numbers stay stable, which fits allocation picking the lowest free numbers.
  - Raising it reactivates the most recently removed row for each missing number before inserting new rows, so a GPU that comes back keeps its row and history. In practice there is at most one row per number.
  - `servers.gpu_count` always equals the number of active rows. Tests check this after every path.
- **Concurrency:** Updating or deleting a server first takes `SELECT … FOR UPDATE` on the server row. Two concurrent `gpu_count` changes are applied one after the other, each seeing the other's result. A test sends twelve concurrent changes and checks the invariant; with the lock removed, it fails.
- **Allocations (Phase 4):** Reducing `gpu_count` must be refused when the GPUs being removed have future active allocations, and deleting a server when it has future active reservations. Allocations do not exist yet, so for now every reduction and deletion is allowed. Both checks have a single home, `_ensure_gpus_removable` and `_ensure_server_deletable`, which are called on every path and currently do nothing.
- **Phase 4 locking note:** With hard deletes, the foreign key from allocations to GPUs would have serialized an allocation against a concurrent removal, since an FK check takes a `KEY SHARE` lock that conflicts with `DELETE`. Soft removal is an `UPDATE` of a non-key column, which does not conflict with `KEY SHARE`. So allocation has to lock the server row `FOR SHARE` (which conflicts with the `FOR UPDATE` taken here) and select only GPUs with `removed_at IS NULL`.

### Who sees which labs and servers
- **Decision:** Admins see everything. Students see active labs, and servers that are not `retired` in active labs. The rule lives in the service, so it applies to the list, to every filter, and to the detail endpoints. A hidden lab or server returns 404 to a student, not 403.
- **Why:** Retired hardware and closed labs are noise when choosing where to book. A 404 avoids confirming that a hidden record exists. Servers in `maintenance` stay visible, because students should see that the machine exists and is temporarily unavailable.
- **Filters:** `lab_id`, `gpu_model`, `min_gpu_memory_gb` (memory per GPU), `min_gpus` (compared with the server's GPU count), and `status`. The page total counts only matching servers.

### Request bodies that name a missing lab
- **Decision:** Creating or moving a server to a lab that does not exist returns 422 `unknown-lab`. A missing lab in the URL path returns 404 `lab-not-found`.
- **Why:** In the body, the lab ID is an invalid input value, not a missing resource at the requested URL.

### Uploads
- **Context:** Avatars and server images are uploaded by the browser directly to storage, so the API never handles the bytes. A presigned URL is a credential handed to the client, so the rules have to hold even if the client misbehaves.
- **Who may upload what:** `POST /uploads/presign` takes a `purpose`. `avatar` is open to every signed-in user, and `server_image` is admin-only (403 otherwise). One endpoint with authorization per purpose keeps the signing and checking code in one place.
- **Keys:** The server generates every key: `avatars/{user_id}/{random}.{ext}` and `servers/{random}.{ext}`. Clients never choose keys, so they cannot overwrite each other's objects.
- **Limits:** PNG, JPEG, or WebP only. Avatars up to 2 MiB, server images up to 5 MiB.
- **Presigned POST over presigned PUT:** A presigned POST carries a signed policy with an exact key, an exact `Content-Type`, and `content-length-range [1, limit]`, and storage enforces it. A presigned PUT can fix the length too (the local storage server rejected a mismatched signed `Content-Length`), but only to one exact size declared in advance. The POST policy expresses the actual rule, a range. The size the client declares when asking for a URL is checked only to fail fast.
- **Checked before saving:** Nothing trusts the upload having gone through the policy. Before a key is saved on a profile or server, `verify_upload`:
  - rejects keys outside the caller's own prefix for that purpose (another user's avatar, nested paths), with 422 `invalid-upload`;
  - HEADs the object and returns 422 `upload-not-found` if nothing is there;
  - deletes the object and returns 422 `invalid-upload` if it is too large, empty, or not an allowed type.
  The checks run in the same transaction as the change, so a failed check leaves the record untouched.
- **Replacing and removing:** Saving the same key again does nothing. When a key is replaced or removed, or its server or user is deleted, the old object is deleted only after the change has committed. The service returns the superseded key, and the router queues the delete as a background task. The request's transaction commits before the response is sent and background tasks run after it, while a request that fails (including at commit) returns an error response without the task. A failed delete is logged and does not fail the request.
- **Blank storage settings:** An env file line such as `S3_ACCESS_KEY_ID=` is treated as unset. Passing an empty string to boto3 would count as an explicit empty key and skip the default credential chain, which is how production gets the instance role's credentials. A test checks that blank keys fall through to the chain.

### Download URLs that browsers can cache
- **Context:** The uploads bucket is private, so images are served through presigned GET URLs. A URL signed at the current time is different on every request, so the browser downloads the same image again on every page load.
- **Decision:** Sign as of the start of the current hour, with a two-hour expiry. Every request within an hour gets the same URL for the same object, and every URL stays valid for at least an hour after it is handed out.
- **How the time is fixed:** botocore's presigner reads the clock inside `add_auth`. Patching that clock is global state, and HEAD and DELETE calls run in worker threads at the same time. Instead, a small subclass of botocore's `S3SigV4QueryAuth` takes the signing time as a constructor argument and sets it on the request before running the same signing steps. The request is built as an `AWSRequest` for the public endpoint, so no shared state is touched. A unit test checks that the result equals botocore's own `generate_presigned_url` at the same time, including keys with spaces and `+`. An integration test downloads through such a URL from the local storage server.
- **Caveat:** With temporary credentials (the instance role), a URL also stops working when the credentials it was signed with expire. Rotated credentials also change the URL within the hour. Both only cost a reload or a cache miss.

### Known gaps for Phase 5
- **Orphaned uploads:** Uploads that are never saved on a record, and objects whose delete failed, stay in the bucket. Options: an S3 lifecycle rule on a pending prefix, with keys moved on save, or an ARQ job that lists objects and removes those no record refers to and that are older than the upload URL lifetime.
- **Presign rate limiting:** `POST /uploads/presign` has no rate limit. Any signed-in user can request many upload URLs and fill the bucket within the per-file limit. The fix is a per-user fixed-window counter in Redis, like the login limits, plus a cap on stored bytes per user if abuse shows up.
