#!/bin/sh
# ---------------------------------------------------------------------------
# Container startup script (web service only).
#
# Model preparation is NOT done here: the dedicated `embedding-init` Compose
# service downloads and verifies the embedding model into the persistent
# model_cache volume, and this container is only started after that service
# has exited successfully (depends_on: service_completed_successfully).
# Every Gunicorn worker then warms the model up before serving (see
# gunicorn.conf.py), so uploads never trigger a download.
#
# 1. Wait for PostgreSQL to accept connections (core.wait_for_db).
# 2. Apply database migrations (no manual setup step required).
# 3. Recover documents interrupted by a previous shutdown (they would
#    otherwise stay 'pending'/'processing' forever).
# 4. Collect static files and hand over to Gunicorn (worker count via
#    WEB_WORKERS; per-worker embedding warm-up via gunicorn.conf.py).
#
# The application starts even without an OPENROUTER_API_KEY: document
# management and the Admin stay usable; only question answering reports a
# clear configuration error until a key is provided.
# ---------------------------------------------------------------------------
set -e

echo ">> Waiting for the database to become available..."
python manage.py wait_for_db

echo ">> Applying database migrations..."
python manage.py migrate --noinput

echo ">> Recovering documents interrupted by a previous shutdown..."
python manage.py reset_stale_processing

echo ">> Collecting static files..."
python manage.py collectstatic --noinput --clear > /dev/null

echo ">> Starting application server (workers: ${WEB_WORKERS:-2}, embedding warm-up per worker: ${EMBEDDING_WARMUP_ON_START:-1})..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${WEB_WORKERS:-2}" \
    --timeout 120 \
    --access-logfile -
