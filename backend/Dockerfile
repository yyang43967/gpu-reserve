# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
# The tokenizer's encoding file, fetched once at build time instead of at first use.
ENV TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken
RUN .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
# The knowledge base lives at the repository root; Compose passes it as a named build
# context (additional_contexts: kb).
COPY --from=kb . ./kb

FROM python:3.12-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken \
    KB_DIR=/app/kb
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
