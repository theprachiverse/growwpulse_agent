FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first for layer caching. No 'embeddings' extra (pulls torch).
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra web --extra streamlit

COPY config ./config
COPY .streamlit ./.streamlit
COPY streamlit_app.py ./

ENV PATH="/app/.venv/bin:$PATH" \
    PULSE_DATA_DIR=/data \
    PYTHONUNBUFFERED=1

# Railway injects $PORT; mount the Volume at /data.
CMD ["sh", "-c", "streamlit run streamlit_app.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true"]
