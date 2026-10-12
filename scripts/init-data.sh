#!/usr/bin/env bash
# Create compose/data/ owned by the UID/GID the containers run as.
set -euo pipefail
cd "$(dirname "$0")/../compose"
# UID is a readonly bash variable, so read .env instead of sourcing it.
env_get() { [[ -f .env ]] && sed -n "s/^$1=//p" .env | tail -n1; }
uid="$(env_get UID)"; uid="${uid:-1000}"
gid="$(env_get GID)"; gid="${gid:-1000}"
mkdir -p data/wikis data/config
if [[ "$(stat -c %u:%g data)" != "$uid:$gid" ]]; then
  if [[ "$(id -u)" == "$uid" ]]; then
    chown -R "$uid:$gid" data
  else
    echo "data/ must be owned by $uid:$gid; run: sudo chown -R $uid:$gid $(pwd)/data" >&2
    exit 1
  fi
fi
echo "compose/data ready (owner $uid:$gid)"
