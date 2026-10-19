FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first for layer caching. No 'embeddings' extra (pulls torch).
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra web

COPY config ./config
COPY deploy_data ./deploy_data
COPY scripts/railway_start.sh ./railway_start.sh
RUN chmod +x railway_start.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PULSE_DATA_DIR=/data \
    PYTHONUNBUFFERED=1

# Railway injects $PORT; mount the Volume at /data.
CMD ["./railway_start.sh"]
