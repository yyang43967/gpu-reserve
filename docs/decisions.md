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
  - **Outcome:** filled in during Phase 4. See "Reducing, retiring, and deleting servers with reservations".
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

## Phase 4 — Reservations

### Tables
- **Context:** A reservation books `gpu_count` GPUs on one server for a time range. The database has to be the final authority on double-booking, not application code.
- **Decision:**
  - `reservations` holds the request: user, server, GPU count, `[starts_at, ends_at)`, status, purpose, the decision fields, and `created_via`.
  - `gpu_allocations` holds one row per GPU held, with its own copy of the time range and an `active` flag.
  - `active` is true only while the reservation is `pending` or `approved`. Every status change clears it in the same transaction.
  - An exclusion constraint forbids overlapping active allocations of the same GPU:
    ```sql
    EXCLUDE USING gist (gpu_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (active)
    ```
  - `active` is `NOT NULL` with a default of true. `WHERE (active)` skips NULL rows, so a nullable flag would let a row escape the constraint.
  - Ranges are half-open, so one booking can start exactly when another ends.
- **Foreign keys:**
  - `reservations.user_id` and `reservations.server_id` are `RESTRICT`. Users and servers with booking history are deactivated or retired, never deleted.
  - `gpu_allocations.gpu_id` is `RESTRICT`. GPU rows are soft-removed in any case.
  - `decided_by` is `SET NULL`, so deleting an admin who decided some reservations does not fail. The audit log keeps the actor's id without a foreign key.
- **The GPU number is not copied onto allocations.** The read model joins `gpus` for it. A copy would have nothing keeping it equal to `gpus.index`, and the join is cheap.
- **Migration check:** the migration drift check does not compare exclusion constraints. A test asserts the exact `pg_get_constraintdef` of the migrated constraint instead.

### GPU allocation and double-booking prevention
- **Options considered:**
  1. SERIALIZABLE isolation. Correct, but any serialization failure needs a retry of the whole request. Unrelated bookings on the same pages can also abort each other, and the guarantee lives in the isolation level rather than in a visible rule.
  2. `SELECT … FOR UPDATE` on the free GPU rows, optionally with `SKIP LOCKED`. This serializes bookings per GPU without retries. But a lock on a GPU row says nothing about time: two bookings for different, non-overlapping times on the same GPU would wait for each other, and correctness would depend on every writer remembering to lock.
  3. A per-server advisory lock around allocation. Simple and retry-free, but every booking on a server waits for every other, even for different GPUs or times.
  4. The exclusion constraint, with optimistic retry (chosen).
- **Decision:**
  - In one transaction, select the server's active GPUs with no overlapping active allocation, ordered by number.
  - Take the lowest `k` and insert the reservation and `k` allocations.
  - If the constraint fires, retry with fresh availability.
  - If fewer than `k` GPUs are free, return 409 `gpu-unavailable`. If the first attempt and three retries all lose to other bookings, return 409 `allocation-contention`.
- **Why it is correct:** the constraint holds no matter how a row is written. Under READ COMMITTED, an insert that overlaps an uncommitted allocation waits for that transaction, then fails once it commits. The retry therefore always sees the winner.
- **Retry details:**
  - Up to three retries after the first attempt (`MAX_ALLOCATION_RETRIES = 3`), so at most four attempts. The first version counted three attempts in total. It was changed to count retries, which is the documented behavior; the extra round costs one more savepoint under contention and nothing otherwise.
  - Each attempt runs in a savepoint (`begin_nested`). A failed attempt rolls back only its own inserts. The user and server locks taken before the savepoint stay held.
  - Each attempt builds new `Reservation` and `GpuAllocation` objects. Nothing from a rolled-back savepoint is reused.
  - The retryable error is recognized by SQLSTATE, not by message text: `23P01` (exclusion violation) and `40P01` (deadlock detected). Any other database error, such as a foreign key violation, propagates on the first attempt. A test checks that.
  - Allocations are inserted in ascending GPU number. An insert that conflicts waits for the other transaction. If every transaction inserts in the same order, a transaction only ever waits for one that has already passed that GPU number, so waits cannot form a cycle. `40P01` is retried only as a defensive measure; this ordering should prevent it.
- **Weakness — lowest-k herding:** concurrent requests all aim at the same lowest free GPUs. Under heavy contention, one request wins per round and the rest retry. A request can therefore run out of attempts while GPUs are still free. A test with 50 concurrent requests for the last 2 GPUs is fine, because the free GPUs run out before the attempts do. The upgrade paths are:
  - choose randomly among the free GPUs on retries, which spreads contenders;
  - take a short per-server advisory lock only after the first conflict;
  - raise the attempt limit, with backoff.

### Rule order and the server lock
- Before any lock, only the shape of the request is checked: aware datetimes normalized to UTC, `ends_at > starts_at`, and `gpu_count >= 1`.
- Then the user's advisory lock is taken, then the server row `FOR SHARE`.
- Every rule that reads server or lab data runs after the server lock: grid, horizon, `max_booking_hours`, GPU count, peak hours, server status, and lab status. Every server change (gpu_count, status, limits, approval flag, delete) takes `FOR UPDATE` on the same row, so a booking sees exactly one version of the server.
- A test holds a booking open and shows that a concurrent `max_booking_hours` edit waits for it.

### Reducing, retiring, and deleting servers with reservations
- **Reducing `gpu_count`:**
  - Refused with 409 `gpus-in-use` while an active allocation with `ends_at > now` sits on a GPU being removed.
  - Pending reservations count, because they hold their GPUs.
  - The error names the GPU numbers and how many reservations hold them.
- **Retiring:** refused the same way while any GPU is held. Students no longer see retired servers, so they would otherwise hold bookings on hardware they cannot find.
- **Maintenance:** only stops new bookings. Existing ones stay.
- **Deleting:**
  - A server with any reservation, in any status, gets 409 `server-has-reservations` and the advice to retire it instead.
  - The `servers → gpus` cascade still deletes the GPU rows of a server that was never booked.
  - For a server with history, the `RESTRICT` foreign keys from reservations and allocations stop the cascade. A delete that races past the check therefore fails with SQLSTATE `23503`, which maps to the same error.
- **Time:** all three checks run under the server's `FOR UPDATE` lock and use the time the caller passes in, never SQL `now()`, so tests can fix the clock.

### GPU removal versus booking: two layers
- **The problem:** removing GPUs is a soft removal, an UPDATE of `gpus.removed_at`. An UPDATE of a non-key column takes a lock that does not conflict with the `KEY SHARE` lock a foreign key check takes. So inserting an allocation on a GPU does not, by itself, stop that GPU from being removed at the same moment.
- **Layer 1, `FOR SHARE` on the server row:** covers the window from reading which GPUs are free to inserting the reservation. Bookings share this lock, so they do not block each other. A reduction's `FOR UPDATE` excludes it.
  - If the reduction comes first, the booking waits before it reads availability, then sees the removal.
  - Without this lock, the booking would read GPUs 2 and 3 as active while the removal is uncommitted. It would block later, when inserting the reservation, with its choice already made, and then put an allocation on a removed GPU. A negative control shows exactly that.
- **Layer 2, the foreign key from `reservations.server_id`:** inserting the reservation takes `KEY SHARE` on the server row, which conflicts with `FOR UPDATE`. From that insert until the booking commits, a reduction waits. Once the booking commits, the reduction's check sees the allocation.
- **Why the booking-first control cannot fail:** if the booking goes first and `FOR SHARE` is removed, the reduction still waits on layer 2 and then gets `gpus-in-use`. Removing `FOR SHARE` alone cannot produce a race in that order. A test pins this down, so a later change that weakens layer 2 is noticed.

### Quota enforcement under concurrency
- **Options considered:**
  1. A counter column per user and week, updated in the booking transaction. Fast reads, but it duplicates data that can drift, and moving or cancelling bookings has to maintain it.
  2. Recomputing usage from reservations under a row lock on the user (`SELECT … FOR UPDATE` on `users`). It works, but it also blocks unrelated writes to the user row, such as profile changes, for the length of every booking.
  3. Recomputing usage under a transaction-scoped advisory lock keyed on the user (chosen).
- **Decision:**
  - `pg_advisory_xact_lock(hashtext(user_id))`, then compute the usage and insert.
  - `hashtext` gives 32 bits. Two users who share a hash only wait for each other a little; correctness does not depend on the key being unique.
- **What the lock covers:**
  - the user's own bookings (the active-reservation count and the quota);
  - admin quota changes;
  - deactivation.
  The admin paths take the lock before updating the users row.
- **Stale quota, found during implementation:** the booking read the quota from the `User` object loaded when the request was authenticated, before the lock. An admin lowering the quota at the same moment was therefore invisible to it. The booking now re-reads `is_active` and the quota after taking the lock. A deterministic test holds the admin's change uncommitted, confirms the booking is blocked, commits, and checks that the booking gets `quota-exceeded`. Removing either the lock on the admin side or the re-read makes the test fail.
- **Which weeks are checked:** every quota week the new booking overlaps, not only the current one. Otherwise a booking that spills past Monday 00:00 could overfill next week.
- **Fixed quota timezone:**
  - The quota week runs from Monday 00:00 to the next Monday 00:00 in one fixed zone, whichever lab a booking is in. Usage is summed across all labs.
  - The zone is its own setting, `QUOTA_TIMEZONE` (default `America/Los_Angeles`), validated like a lab zone (IANA name, whole-half-hour offsets). It is passed into the booking and quota functions as an argument.
  - It is deliberately not derived from the default zone for new labs. The two only look related: one is a form default, the other defines when every user's week resets. If the quota zone followed the lab default, changing that default would silently move every quota week, and bookings already accepted could end up over the quota in the new weeks.
  - A per-lab week was rejected: one user's bookings would be counted in different week windows depending on the lab. Near a week boundary, hours booked in one lab just before its Monday and in another lab just after a later Monday land in different weeks. The user could then hold more than the quota within a single week of either zone.
  - Weeks that contain a daylight saving change are 167 or 169 hours long.
- **`/me/quota`** uses the same functions as the check (`_load_quota_bookings` and `used_gpu_minutes`), so the number shown is the number enforced. Hours are fractional, because bookings are in half hours.
- **`/me/quota?at=`** returns the week containing any instant, not only the current week. A booking is checked against the week it falls in, so showing only this week's numbers would let the dialog say "40 hours left" for a booking next week that is then refused. The booking dialog asks for the week of the chosen start. A booking that crosses Monday is still checked against both weeks; the dialog shows the start's week, and the API's `quota-exceeded` detail names the week that overflowed.

### Active-reservation limit
- **Rule:** a user may hold at most four reservations that are `pending` or `approved` and have not ended (`ends_at > now`).
- **Running bookings count.** A running booking still holds GPUs. The limit exists to stop one person from holding many slots at once; leaving running ones out would let a user with four running bookings queue four more.
- **Pending past its start counts** until the expiry job marks it `expired`, because its GPUs are still held until then.

### Peak-hours limit per booking
- **Rule:** the GPU-hours a single booking spends inside weekday 09:00–18:00 (lab time) may not exceed 4.
- **Summed across every peak window the booking touches**, not checked per day. The limit is about how much of the busiest time one booking takes, so splitting it by day would let a long booking take 4 GPU-hours of peak time on each of several days.
- **In practice** this only differs from a per-day check when a booking spans two peak windows, which needs more than 15 hours. That is possible only on servers where an admin raised `max_booking_hours` above the default 12.

### Approval only before the start
- **Rule:** approving a `pending` reservation whose start has passed returns 409 `invalid-reservation-state`. Rejecting it is still allowed, since that releases the GPUs.
- **Why:** a pending reservation past its start belongs to the expiry job, which turns it into `expired`. If approval stayed open, an admin and the job could act on the same row at the same moment. The row lock would make the outcome consistent, but it would depend on who came first. The student would also get an approval for time that has already partly passed. A clear cut-off at the start gives one owner per transition: admins before the start, the job after it.

### Lock order
- Bookings take the user lock, then the server row.
- Deactivation takes the user lock, then the user's reservation rows.
- Approve, reject, and cancel take the reservation row. Approve then takes the server row `FOR SHARE` (see "Approval checks the server").
- Server changes take only the server row.
- No path takes a reservation row and then the user lock, or the server row and then the user lock, or the server row and then a reservation row, so these paths cannot deadlock against each other.

### Time grid and rules
- **Granularity is checked in UTC:** `starts_at` and `ends_at` must be whole multiples of 30 minutes since the Unix epoch.
  - This is the same as local :00 and :30 in every zone whose offset is a whole number of half hours.
  - Zones with offsets such as +05:45, +08:45, and +12:45 are rejected for labs. The check looks at the offset on 1 January and 1 July of this year and next.
  - Bookings stay on the grid even if a lab's time zone is changed later.
  - Availability slots and the early-end rounding use the same grid.
- **Local time where it matters:** peak hours use the lab's zone (weekdays 09:00–18:00, at most 4 GPU-hours per booking inside that window). Quota weeks use the fixed quota zone.
- **422 vs 409:**
  - Rules that depend only on the request and the server's settings return 422: grid, start in the past, more than 14 days ahead, too long, too many GPUs, peak-hours limit.
  - Rules that depend on other bookings or on the server's state return 409: GPUs taken, quota, four active reservations, server or lab not open.
- **`booking-too-short` cannot be reached through the API.** With both ends on the grid and the end after the start, every accepted range is at least 30 minutes. Shorter or misaligned ranges are reported as `invalid-time-grid` or `invalid-time-range`. The rule is kept as a defensive check and has a unit test with a raised minimum.

### Cancelling and ending early
- **Order of checks:** status first, then time.
  - `pending`, before or after its start: `cancelled`.
  - `approved` with `now <= starts_at`: `cancelled`. "Started" means strictly after the start.
  - `approved` with `starts_at < now < ends_at`: `completed`, ended early.
  - Already ended, or in a terminal status: 409 `invalid-reservation-state`.
- **Students can end their own running reservation early**, not only admins. Ending early gives GPU time back to everyone and lowers the student's own quota use, and there is nothing to protect by forbidding it.
- **The new end** is the first grid boundary at or after now. New bookings can only start on the grid and in the future, so that is exactly where the freed time begins. In the last half hour the boundary is the original end: the status still becomes `completed` and the end does not change. The original end is kept in the audit payload.
- **Allocations become inactive on early end**, consistent with "active only while pending or approved", and their end is shortened too.
  - The rest of the current half hour shows as free in availability.
  - It cannot be booked, because a new booking must start on a later boundary.
  - This is accepted. The alternative, keeping completed allocations active, would mix states.

### Deactivating a user
- **Decision:** deactivation takes the user lock and, in the same transaction:
  - cancels the user's pending reservations and approved ones that have not started;
  - ends running ones early with the cancel rule;
  - writes an audit row for each, with the admin as actor and `reason: user_deactivated`.
  Approved reservations that have already ended are left for the completion job.
- **Why now rather than later:** an inactive account holding GPUs blocks other users until someone notices. The cancel logic already existed, so reusing it in the same transaction costs nothing.
- **Bookings in flight:** a booking re-reads `is_active` after taking the user lock and refuses an inactive account with the same 401 the authentication layer gives. Tests cover:
  - a booking that waits for an uncommitted deactivation and is then refused;
  - an approval that waits for the reservation row and then finds the reservation cancelled.
- **Reactivation** does not restore cancelled reservations.

### Approve, reject, and cancel under concurrency
- **Decision:** each transition locks the reservation row (`FOR UPDATE`) and reads the current status after the lock.
- **Approve vs reject:** both require `pending`, so exactly one wins and the other gets `invalid-reservation-state`.
- **Approve vs cancel:** both can succeed in sequence. Approve then cancel ends cancelled; cancel then approve refuses the approval. The guaranteed invariant is that a successful cancel is never lost, and the allocations match the final status.
- **What the lock prevents:** without the row lock, an approval that read `pending` before a cancel committed would write `approved` over it. The negative control shows that result: an `approved` reservation whose allocations the cancel already released, holding no GPUs.

### Reservation visibility
- Reading, listing, and cancelling a reservation is allowed for its owner and for admins. Anyone else gets 404 `reservation-not-found`, not 403.
- This does not depend on whether the server is visible. A student keeps seeing, and can still cancel, their own reservation on a server that was retired or whose lab was deactivated.
- Creating a reservation and reading availability still apply the server visibility rules.

### Listing and availability
- **List filter:** `from` and `to` select reservations that overlap the window (`ends_at > from` and `starts_at < to`), as documented on the parameters.
- **List loading:** related rows (server, lab, user, allocations, GPUs) are loaded with one query per relationship. A test checks that a page of 2 and a page of 12 cost the same number of statements.
- **List order:** newest start first by default, which suits a history. `order=starts_at` gives soonest first, which the approval queue needs. Sorting a page in the browser instead would be wrong once the queue is longer than one page.
- **Availability:**
  - The window is at most 14 days, defaults to the next 7, and is widened to whole slots.
  - The response is `{server_id, timezone, gpu_count, slot_minutes, starts_at, ends_at, runs}`, where each run is a stretch of slots with the same number of free GPUs. Runs keep a week's response small when nothing changes for hours.
  - Pending reservations count as busy. Nothing about who booked is returned.
  - It is computed from one query of overlapping active allocations. Caching comes later.

### Lab time in the browser
- **No date library:** wall times are converted with `Intl.DateTimeFormat#formatToParts`. A local time is resolved by trying the zone's offsets from a day before and a day after, and keeping each candidate that really shows that wall time in the zone. A zone-aware date library would add a dependency for one conversion and a few formatters.
- **Every time shown is in the lab's zone, with its abbreviation**, never the browser's zone. A student in another time zone sees the times the lab sees. Some zones have no common abbreviation, and the browser shows those as "GMT+9".
- **Nonexistent and ambiguous times:**
  - A local time in the spring-forward gap is rejected in the form, on the field that has it.
  - A local time in the fall-back hour resolves to its first occurrence, and the field says so with the zone abbreviation ("01:30 happens twice that day; this is the first one (PDT)"). The second occurrence cannot be booked from the dialog. The API accepts either, given an explicit offset.
- **Time zone picker:** the lab form suggests only zones whose offsets are whole half hours, using the same rule as the API. The field is still free text, and the API's 422 is the authority; it shows under the field. The browser's zone list uses some older names (`Asia/Calcutta`); the API accepts both spellings.

### Booking dialog
- **Fields:** start date and time, end date and time. Times are half-hour options.
- **End date:** it follows the start date until the person picks one. When the end time is at or before the start time on the same date, the end date moves to the next day. That makes overnight bookings and a 00:00 end work without extra steps. An end date the person picked stays where they put it.
- **Errors by problem type:**
  - Known types go under their field with the API's detail text: GPU conflicts and the peak-hours limit under GPUs, duration under the end time, the horizon and grid under the start.
  - Quota, the active-reservation limit, server state, and any unknown type go in an alert above the form, again with the detail text.
  - The API's details are written for people, so the client does not keep its own copy of each message, and a new rule shows something sensible without a client change.
- **After booking or cancelling:** every reservation query is invalidated (availability, lists, quota). They share one key prefix.

### Availability timeline
- **One row per lab-local day, built from the slots the API returned.** A full day has 48 half hours, but 46 on a spring-forward day and 50 on a fall-back day. Rows are never forced to 48.
- **Rows line up by time of day** under a shared hour axis. A partial first day is padded, and the scroller opens at today's first slot so a phone does not open on blank space.
- **Daylight saving days** are drawn in time order. After the change such a row no longer matches the hour axis, so the change is marked with a divider and a note ("Clocks go back: 01:00–02:00 happens twice (PDT, then PST)"). The alternatives were worse: placing cells by time of day would overlap the repeated hour and leave a hole for the skipped one.
- **Colors:** free, partly free, and full use green, amber, and slate, which stay distinct in both themes. A per-day sentence carries the same information for screen readers.
- **Picking a slot:** clicking a free slot opens the booking dialog with that start.

### Acting on stale data in the browser
- The cancel button and the approve button are decided by the browser's clock and the last fetched status. Near a boundary, or when another admin acts first, the server can disagree.
- **On a 409 from cancel,** the error appears on the reservation and the list is fetched again, so the row shows the server's status.
- **On a 409 from approve or reject,** the error appears on the row and the queue is fetched again. When the refreshed queue no longer contains the request (another admin decided it), the row disappears, so its error moves above the list, naming whose request it was.
- **Approving after the start** is disabled in the queue, with an explanation, because the API refuses it. Rejecting stays available, since it releases the GPUs.

### Concurrency tests
- **Separate connections:** every participant has its own session and connection, from an engine whose pool is larger than the number of participants. Participants start together behind a barrier.
- **Bounded waits:** every barrier wait and every lock-wait poll has a timeout, so a broken protection fails the test instead of hanging it. Committed data is truncated explicitly afterwards.
- **What they cover:**
  - 50 bookings for the last two GPUs: exactly two succeed, and no active allocations overlap.
  - Ten bookings that each fit the quota alone: exactly three succeed.
  - Booking vs `gpu_count` reduction, in both orders.
  - A booking vs a server edit.
  - Quota change and deactivation vs a booking.
  - Approve vs reject and approve vs cancel.
- **Negative controls:** each protection has a control that removes it (drops the constraint, replaces a lock with a plain read) and holds the participants at the race window. That shows the race happens, deterministically, rather than hoping a race occurs by chance. The one exception is the booking-first reduction order, explained above.

### Known gaps for Phase 5
- **No expiry yet:** a pending reservation past its start keeps its allocations until the expiry job exists, and approved reservations past their end stay `approved` until the completion job.
- **No notifications** on approve, reject, cancel, or deactivation.
- **Lab deactivation and maintenance** leave existing bookings in place. Their owners can still see and cancel them, but nobody tells them.
- **Approve does not check the server's state:** an admin can approve a pending reservation on a server that has since gone into maintenance.
  - **Outcome:** fixed in Phase 5. See "Approval checks the server".

## Phase 5 — Jobs, search, polish

### Notifications: a transactional outbox
- **Context:** approvals, rejections, cancellations, expiries, and reminders send email. Email goes to an outside system, so it cannot be part of the database transaction that makes the change.
- **Options considered:**
  1. Send from the request after commit. The request waits on SMTP, and a crash or SMTP outage after the commit loses the email.
  2. Enqueue an ARQ job after commit. Faster for the request, but a Redis outage or a crash between the commit and the enqueue still loses it.
  3. Write a `notifications` row in the same transaction as the change, and deliver rows from a worker (chosen).
- **Decision:** `notify_owner` adds a row next to the change. A committed change always has its row; a rolled-back one never does. A test cancels, flushes, rolls back, and finds no row.
- **Shape:** one row per `(reservation_id, kind)`, enforced by a unique constraint, with `status` (`pending`, `sent`, `skipped`, `failed`), `attempts`, `next_attempt_at`, and `last_error`. A partial index on `next_attempt_at WHERE status = 'pending'` serves the dispatcher.
- **At least once:** a send and the update that records it cannot be atomic. A crash in between can send a message twice. That is accepted: a duplicate notice is a nuisance, a missing one can cost someone their booking.
- **Stale reminders:** the dispatcher re-reads the reservation before sending. A reminder whose reservation is no longer approved, or has already started, is marked `skipped`. Other kinds report a change that did happen and are always sent.

### Who is notified
- **Rule:** the owner is emailed when someone else changed their reservation, and always for expiry and reminders, which have no actor.
  - Admin approval, rejection, or cancellation (including ending a running booking early): email.
  - Deactivation releases every booking with the admin as the actor, so the user gets one email per released booking, even though the account is now inactive. They need to know.
  - A student cancelling or ending their own booking: no email. They just did it.
  - An admin deciding their own request: no email, by the same rule.
- **No "inactive recipients are skipped" rule.** It would contradict the deactivation case, and nothing else needs it: deactivation releases all bookings first, so an inactive account never gets reminders.
- **Completion** sends nothing. A booking ending on time is not news.

### Sweeping jobs and idempotency
- **Jobs:** `expire_pending` every minute, `complete_finished` and `queue_reminders` every five minutes. Each is a thin ARQ wrapper around a service function that takes `now`.
- **`expire_pending`** is set-based, in one transaction: an ORM `UPDATE … SET status = 'expired' WHERE status = 'pending' AND starts_at <= now RETURNING id, user_id`, then one UPDATE deactivating those reservations' allocations, an audit row each (`actor_id NULL`), and one INSERT of `expired` notifications. Only the ids the UPDATE returned are touched afterwards, and those rows are locked by it until commit.
- **Expiry against approval:** approval requires `now < starts_at` and expiry `starts_at <= now`, so each moment has one owner. If an approval holds the row when the job's UPDATE reaches it, the UPDATE waits, then re-checks its WHERE clause against the committed row (READ COMMITTED). The row is now `approved`, so it is skipped and keeps its GPUs. If the job gets there first, the approval waits on its `FOR UPDATE`, then reads `expired` and is refused. Tests cover both orders; a negative control shows that an approval without its row lock would write `approved` over an expiry and leave a booking holding no GPUs.
- **`complete_finished`** is one statement: a CTE updates approved reservations with `ends_at <= now` to `completed`, deactivates their allocations, and inserts system audit rows (`actor_id NULL`). `audit_log.actor_id` was already nullable; a test checks the migrated column.
- **Idempotent by construction:** each statement selects by state (`status = 'approved' AND ends_at <= now`), and the state it writes no longer matches. A second run finds nothing. Reminders rely on the unique `(reservation_id, kind)` constraint with `ON CONFLICT DO NOTHING`. Tests run each job twice.
- **Races with requests:** the job's UPDATE and a request's `FOR UPDATE` both lock the row. Under READ COMMITTED, whichever waits re-reads the row after the other commits, so a late cancel finds a completed reservation and is refused, and a job finds a cancelled one and skips it.
- **No overlapping runs, two layers:**
  1. ARQ cron jobs are `unique`: a tick has one job id, so several workers enqueue it once.
  2. Consecutive ticks can still overlap if one runs long. Each sweeping job takes `pg_try_advisory_xact_lock(1, hashtext(job_name))` and returns at once when another run holds it. The two-key form uses a key space Postgres keeps separate from the single-key user locks, so a job name can never collide with a user id's hash. A test holds the single-key lock on the same hash and shows the job still runs.
  The statements are idempotent without the lock. The lock keeps two runs from queueing behind each other on the same rows and doing the same work twice.
- **Late reminders:** a reminder is queued for approved reservations starting within the next hour. A booking made less than an hour before its start is picked up by the next run, so its reminder can arrive up to five minutes after the booking. Accepted: the person booked moments ago.

### Delivering notifications: one row per transaction
- **Options considered:**
  1. Lock due rows with `FOR UPDATE SKIP LOCKED`, send, mark, and commit in one transaction (chosen, with one row per transaction).
  2. Claim rows by setting an `in_flight` status with a lease expiry and committing, then send outside any transaction and record the result in a second one. Expired leases are reclaimed.
  3. One dispatcher at a time behind an advisory lock.
- **Decision:** a loop of short transactions. Each one does `SELECT … WHERE status = 'pending' AND next_attempt_at <= now ORDER BY next_attempt_at LIMIT 1 FOR UPDATE SKIP LOCKED`, prepares the email, sends it, marks the row `sent`, `skipped`, or a retry or failure, and commits. It stops when no row is due or after a per-tick cap (`batch_size`, 50).
- **Why:**
  - **Scale:** one instance and a low volume of email. The simplest design that is correct is enough.
  - **Crash recovery is automatic.** Row locks die with the connection, so a crashed dispatcher leaves its row `pending` and the next tick picks it up. There is no lease to tune and no extra state.
  - **The duplicate window is one email**, the same as with leases. Committing per row means a crash between the send and the commit can repeat only the message in flight, never a whole batch.
  - **The lock held during the send blocks nobody.** Only the dispatcher touches notification rows. Holding one through an SMTP call costs one pooled connection for that long, nothing else.
- **What each part does** (each has a test that removes it):
  - `FOR UPDATE` is what prevents double sends. Without it, two dispatchers read the same row and both send it.
  - `SKIP LOCKED` is what keeps dispatchers independent. Without it, a second dispatcher still never double-sends: it waits for the row, re-reads it, and finds it sent. But it waits behind a stuck send instead of delivering other rows.
  - The per-job advisory lock used by the sweeping jobs is not used here. It would serialize dispatchers and make `SKIP LOCKED` pointless.
- **Any failure is an attempt**, including an error while building the message, not only transport errors. A row that always fails is given up on after five attempts instead of stopping every tick at the same place. The error is kept in `last_error` (cut to 1,000 characters) and logged.
- **When to change:** move to leases (option 2) if sends get slow enough to put pressure on the connection pool, or if the number of workers grows. That removes the connection held during the network call, at the cost of a lease length to choose and rows stuck until it expires after a crash.
- **Accepted race, stale reminders:** the staleness check reads the reservation without locking it. A cancellation that commits between that read and the send can let one reminder for a cancelled booking through. Locking the reservation row during an SMTP call would block the user's own cancel for the length of the send, which is worse than one unneeded reminder.

### Email
- **Transports:** an `EmailSender` interface with two implementations. `SmtpSender` uses the standard library's `smtplib` in a worker thread (Mailpit locally). `SesSender` uses the SES v2 API through boto3 with the instance role's credentials, so production needs no SMTP password. `EMAIL_BACKEND` picks one. No new dependencies.
- **Bodies:** plain text and HTML, from `string.Template`. Every value placed in the HTML is passed through `html.escape`, because purposes and rejection reasons are typed by users. Names and hostnames are escaped too, rather than deciding field by field which ones are trusted. `string.Template` does not re-read substituted values, so a `$` in a purpose stays literal. Tests cover `<script>` in the purpose and the reason.
- **Times** are shown in the lab's time zone with its abbreviation, as in the app.

### Approval checks the server
- **Rule:** approving returns 409 `server-unavailable` when the server is not `available` or its lab is inactive. Rejection is still allowed and releases the GPUs.
- **Locking:** after the reservation row, approval takes the server row `FOR SHARE`, like a booking. A concurrent switch to maintenance (which takes `FOR UPDATE`) either commits first and is seen, or waits for the approval. Server changes never lock reservation rows, so this order cannot deadlock.

### Upload URL rate limit
- **Rule:** at most 30 presigned upload forms per user per 10 minutes, as a fixed-window counter in Redis (`presign:user:{id}`), the same mechanism as the login limits. Over the limit: 429 `too-many-requests` with `Retry-After`. It fails open on a Redis outage, like the other limits.
- **Why per user:** each form lets its holder store one file of up to 5 MiB, and every caller is signed in, so the account is the natural unit.

