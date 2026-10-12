# syntax=docker/dockerfile:1
#
# wiki-inbox: MCP server (Streamable HTTP, /mcp on :8081) for the llm-wiki inbox.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc

FROM ${UV_IMAGE} AS uv

# ── Builder: resolve the locked environment into /app/.venv ──────────────────
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE}

ARG UID=1000
ARG GID=1000

LABEL org.opencontainers.image.title="wiki-inbox" \
      org.opencontainers.image.description="MCP server for the llm-wiki ingestion inbox"

RUN groupadd --gid "${GID}" wiki \
 && useradd --uid "${UID}" --gid "${GID}" --home-dir /home/wiki --create-home --shell /usr/sbin/nologin wiki \
 && chmod 1777 /home/wiki \
 && mkdir -p /wikis /config

COPY --from=builder /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOME=/home/wiki \
    INBOX_LLM_WIKI_CONFIG=/config/config.toml \
    INBOX_PORT=8081

USER wiki
WORKDIR /home/wiki
VOLUME ["/wikis"]
EXPOSE 8081

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('INBOX_PORT','8081'), timeout=2)"]

ENTRYPOINT ["wiki-inbox"]
