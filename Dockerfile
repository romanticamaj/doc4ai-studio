# The web studio and the MarkItDown engine, on CPU. Docling and MinerU install into the envs volume:
#   docker compose exec doc4ai doc4ai setup all
FROM node:22-slim AS web
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm exec vite build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

RUN useradd --create-home --uid 1000 doc4ai \
    && mkdir -p /app /data /library \
    && chown doc4ai:doc4ai /app /data /library
USER doc4ai
WORKDIR /app

ENV DOC4AI_ROOT=/app \
    DOC4AI_DATA=/data \
    DOC4AI_LIBRARY_ROOT=/library \
    DOC4AI_CONFIG=/data/doc4ai.toml \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

COPY --chown=doc4ai:doc4ai pyproject.toml uv.lock README.md LICENSE doc4ai.toml ./
COPY --chown=doc4ai:doc4ai src/ src/
RUN uv sync --frozen --no-default-groups

COPY --chown=doc4ai:doc4ai envs/ envs/
RUN doc4ai setup markitdown && uv cache clean

COPY --from=web --chown=doc4ai:doc4ai /web/dist web/dist
COPY --chown=doc4ai:doc4ai --chmod=755 docker/entrypoint.sh /usr/local/bin/doc4ai-entrypoint

VOLUME ["/data", "/library", "/app/envs"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8765/health', timeout=4)"
ENTRYPOINT ["doc4ai-entrypoint"]
