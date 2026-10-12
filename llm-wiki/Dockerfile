# syntax=docker/dockerfile:1
#
# Container image for the upstream llm-wiki engine
# (https://github.com/geronimo-iia/llm-wiki), built from crates.io.

ARG RUST_IMAGE=rust:1.95-slim-bookworm@sha256:d7482085ff5b415f84dba5647ae71606650bdef00db7aeb69f4b3d170c3e4082
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

# ── Builder ───────────────────────────────────────────────────────────────────
FROM ${RUST_IMAGE} AS builder

ARG LLM_WIKI_VERSION=1.0.1

# libgit2 is vendored (git2 "vendored-libgit2"), so only a C toolchain and
# pkg-config are needed (zlib is built from source by libz-sys if absent).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config \
 && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    cargo install llm-wiki-engine --version "${LLM_WIKI_VERSION}" --locked --root /out

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM ${RUNTIME_IMAGE}

ARG UID=1000
ARG GID=1000
ARG LLM_WIKI_VERSION=1.0.1

LABEL org.opencontainers.image.title="llm-wiki" \
      org.opencontainers.image.description="llm-wiki engine (MCP over Streamable HTTP)" \
      org.opencontainers.image.source="https://github.com/geronimo-iia/llm-wiki" \
      org.opencontainers.image.version="${LLM_WIKI_VERSION}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git tini \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${GID}" wiki \
 && useradd --uid "${UID}" --gid "${GID}" --home-dir /home/wiki --create-home --shell /usr/sbin/nologin wiki \
 && mkdir -p /wikis /config \
 && chown wiki:wiki /wikis /config \
 # HOME must be writable even when compose overrides the UID (`user:`).
 && chmod 1777 /home/wiki

COPY --from=builder /out/bin/llm-wiki /usr/local/bin/llm-wiki
COPY --chmod=0755 entrypoint.sh /entrypoint.sh
COPY config.toml.template /etc/llm-wiki/config.toml.template

ENV HOME=/home/wiki \
    LLM_WIKI_CONFIG=/config/config.toml \
    LLM_WIKI_PORT=8080

USER wiki
WORKDIR /home/wiki
VOLUME ["/wikis", "/config"]
EXPOSE 8080

# The engine only installs a Ctrl-C (SIGINT) handler for graceful shutdown.
STOPSIGNAL SIGINT

# No HTTP health endpoint upstream: check that the port accepts TCP connections.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/${LLM_WIKI_PORT}' || exit 1

ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
