# syntax=docker/dockerfile:1.7

# --- Stage 1: build a virtualenv with deps + the project ---
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (better layer caching). Only lockfile + manifest.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now copy the source and install the project itself.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- Stage 2: minimal runtime ---
FROM python:3.14-slim-bookworm AS runtime

# Run as non-root.
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Copy the prepared virtualenv and source from the builder.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER app

# Entry point: long-polling Telegram bot.
CMD ["nano-bot"]
