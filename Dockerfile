# Closebrief container (v2.0). Single service: FastAPI serves both the API and
# the single-file SPA at /. Pure-Python — no build step, no sentence-transformers
# (prod uses OpenAI embeddings), so the image stays lean.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the pinned runtime before building the app without dependency resolution.
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-deps .

# Then the rest of the source (ui/, eval/, data generator, etc.).
COPY . .

# SQLite/FAISS fallback dir (unused in prod, which sets DATABASE_URL + pgvector).
RUN useradd --uid 10001 --create-home app \
    && mkdir -p data/uploads \
    && chown -R app:app /app

USER app

EXPOSE 8000

# Render (and most PaaS) inject $PORT; bind to it, default 8000 for local runs.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
