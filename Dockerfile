# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# cake-pricing — multi-arch (linux/amd64 + linux/arm64 for rPi5) image.
# Build with uv; run as non-root. See PLANNING.md §5.
# ---------------------------------------------------------------------------

# ---- builder: resolve & install deps into a venv with uv --------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first (cached layer), using only the lock + manifest.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now add the application source and install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- final: slim runtime, non-root ----------------------------------------
FROM python:3.14-slim-bookworm AS final
LABEL name="cake-pricing"

ARG USERNAME=app
ARG USER_UID=1000
ARG USER_GID=1000

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN groupadd -g $USER_GID $USERNAME \
    && useradd -m -u $USER_UID -g $USER_GID -s /usr/sbin/nologin $USERNAME

WORKDIR /app
COPY --from=builder --chown=$USER_UID:$USER_GID /app /app

USER $USERNAME
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
