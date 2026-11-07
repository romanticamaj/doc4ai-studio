#!/bin/sh
# The server listens on every interface inside the container, and doc4ai refuses that without a token.
# Without DOC4AI_TOKEN, one is created once and kept in the data volume.
set -eu

[ -f "$DOC4AI_CONFIG" ] || cp /app/doc4ai.toml "$DOC4AI_CONFIG"

if [ -z "${DOC4AI_TOKEN:-}" ]; then
    if [ ! -s /data/serve.token ]; then
        (umask 077; python -c "import secrets; print(secrets.token_urlsafe(32))" > /data/serve.token)
        echo "Created a server token. Read it with: docker exec <container> cat /data/serve.token"
    fi
    DOC4AI_TOKEN=$(cat /data/serve.token)
    export DOC4AI_TOKEN
fi

exec doc4ai serve --host 0.0.0.0 --port 8765 "$@"
