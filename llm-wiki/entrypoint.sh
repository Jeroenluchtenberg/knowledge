#!/usr/bin/env bash
# Entrypoint for the llm-wiki container. Idempotent: safe on every restart.
set -euo pipefail

CONFIG="${LLM_WIKI_CONFIG:-/config/config.toml}"
TEMPLATE=/etc/llm-wiki/config.toml.template
PORT="${LLM_WIKI_PORT:-8080}"

log() { echo "entrypoint: $*" >&2; }

# 1. Render the global config on first start.
if [[ ! -f "$CONFIG" ]]; then
  log "rendering $CONFIG from template"
  mkdir -p "$(dirname "$CONFIG")"
  sed "s/\${LLM_WIKI_PORT}/${PORT}/g" "$TEMPLATE" > "$CONFIG"
fi

# 2. Env-driven settings. The engine expects a comma-separated host list and
#    matches the Host header without its port.
ALLOWED_HOSTS="${LLM_WIKI_ALLOWED_HOSTS:-localhost,127.0.0.1,llm-wiki}"
llm-wiki config set serve.http_allowed_hosts "$ALLOWED_HOSTS" --global >/dev/null
llm-wiki config set logging.log_format "${LLM_WIKI_LOG_FORMAT:-json}" --global >/dev/null
llm-wiki config set logging.log_path "" --global >/dev/null

# 3. Git identity for engine commits. git2's Repository::signature() reads
#    user.name/user.email from the git config chain (incl. ~/.gitconfig).
git config --global user.name "${GIT_AUTHOR_NAME:-llm-wiki}"
git config --global user.email "${GIT_AUTHOR_EMAIL:-llm-wiki@localhost}"
# Wiki repos live on a volume that may be owned by another UID.
git config --global --replace-all safe.directory '*'

# 4. Optional bootstrap of a first wiki.
if [[ -n "${LLM_WIKI_BOOTSTRAP_WIKI:-}" ]]; then
  name="$LLM_WIKI_BOOTSTRAP_WIKI"
  if ! grep -q '^\[\[wikis\]\]' "$CONFIG"; then
    log "bootstrapping wiki '$name' at /wikis/$name"
    llm-wiki spaces create "/wikis/$name" --name "$name"
    llm-wiki spaces set-default "$name"
  fi
  # Engine commits stage the whole repo; keep Syncthing internals out of git.
  gitignore="/wikis/$name/.gitignore"
  for pattern in '.stfolder' '.stversions/' '.stignore' '.syncthing.*.tmp' '~syncthing~*'; do
    grep -qxF "$pattern" "$gitignore" 2>/dev/null || echo "$pattern" >> "$gitignore"
  done
fi

# 5. Serve MCP over Streamable HTTP (endpoint /mcp).
args=(serve --http ":${PORT}")
if [[ "${LLM_WIKI_WATCH:-false}" == "true" ]]; then
  args+=(--watch)
fi
log "starting: llm-wiki ${args[*]}"
exec llm-wiki "${args[@]}"
