#!/bin/sh
set -e

# Ensure data directory exists and is writable (volume mount may bring root-owned dir)
mkdir -p /app/data
chmod -R 777 /app/data

# Safety net: check if any migrations are pending and run them if needed.
# In normal docker-compose operation the dedicated 'migrate' service handles
# this, but this guard covers standalone container runs and out-of-order
# restarts.  --check exits 0 when all migrations are applied, non-zero when
# any are missing.
if ! uv run python manage.py migrate --check 2>/dev/null; then
    echo "Running database migrations..."
    uv run python manage.py migrate --noinput
fi

echo "Starting application..."
exec "$@"
