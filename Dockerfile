# syntax=docker/dockerfile:1
# FlowPilot AI images for the public demo (docs/adr/0005-public-demo.md).
# Two targets: `api` (FastAPI, demo database only) and `console` (Streamlit). Both run as an
# unprivileged user and contain no secrets: keys and tokens come from the environment at run time.
#
# Multi-stage: dependencies are installed from the lockfile in a builder stage with uv's download
# cache on a BuildKit cache mount, and only the resulting virtual environment is copied into the
# runtime stages. The runtime images contain no uv binary, no build cache and no source tree.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv

# --- build -----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/root/.cache/uv
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

FROM builder AS build-api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM builder AS build-console
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group ui --no-editable

# --- runtime ---------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    HOME=/tmp
RUN groupadd --system --gid 10001 flowpilot \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin flowpilot
WORKDIR /app

FROM runtime AS api
COPY --from=build-api /opt/venv /opt/venv
RUN mkdir -p /app/var/demo && chown 10001:10001 /app/var/demo
USER 10001:10001
EXPOSE 8000
CMD ["flowpilot-demo", "serve", "--public"]

FROM runtime AS console
COPY --from=build-console /opt/venv /opt/venv
COPY .streamlit ./.streamlit
COPY src/flowpilot/ui/app.py ./src/flowpilot/ui/app.py
USER 10001:10001
EXPOSE 8501
CMD ["streamlit", "run", "src/flowpilot/ui/app.py"]
