# ---------------------------------------------------------------------------
# Document QA System - production-ish application image
#
# Multi-stage build keeps the final image small: build wheels/ONNX runtime in
# the "builder" stage, copy only the installed packages into the runtime.
# ---------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential + git are needed by some wheels (chromadb / fastembed deps).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /install
COPY requirements.txt .
RUN pip install --prefix=/install --no-compile -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libgomp1 is required by onnxruntime (fastembed embeddings).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Installed python packages from the builder stage.
COPY --from=builder /install /usr/local

# Application source.
COPY --chown=appuser:appuser . /app/

# Writable directories (Chroma persistence, static files, embedding-model
# cache). No media volume: documents are text-centric, the uploaded DOCX is
# parsed in memory and never stored.
# chmod is required because Windows checkouts do not preserve the executable bit.
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/chroma_data /app/staticfiles /app/model_cache \
    && chown -R appuser:appuser /app/chroma_data /app/staticfiles /app/model_cache

USER appuser

EXPOSE 8000

# The entrypoint waits for PostgreSQL, applies migrations, recovers
# interrupted documents, starts the embedding-model preload in the
# background and then runs Gunicorn (worker count via WEB_WORKERS env).
ENTRYPOINT ["/app/entrypoint.sh"]
