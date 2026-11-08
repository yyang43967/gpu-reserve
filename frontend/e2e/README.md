# End-to-end tests

Playwright drives the built frontend against a separate Docker Compose stack
(`docker-compose.e2e.yml`): Postgres in memory, Redis, the API, the worker, and Mailpit,
on ports 18000 and 18025. It never touches the development database.

From the repository root:

```bash
docker compose -f docker-compose.e2e.yml up -d --build --wait
pnpm --dir frontend exec playwright install chromium
pnpm --dir frontend exec playwright test
docker compose -f docker-compose.e2e.yml down
```

Global setup migrates the database, empties it (`python -m app.cli reset-database`,
which only runs with `ENVIRONMENT=test`), creates an admin with a random password, and
adds one lab with an open server and a server that needs approval. Playwright then
builds the frontend and serves it with `vite preview` on port 4173.

Times are chosen relative to now (tomorrow from 19:00 lab time), so the tests do not
depend on when they run. The browser runs in Tokyo, so every time on screen has to come
from the lab's zone. The approval test waits for the worker's next minute tick to send
the email, so it can take up to a minute and a half.
