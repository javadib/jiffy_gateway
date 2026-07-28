#!/bin/sh
set -e

# Ensure data directory exists and is writable (volume mount may bring root-owned dir)
mkdir -p /app/data

# Recreate virtual environment if missing (e.g. host .venv was shadowed by anonymous volume)
#if [ ! -f /app/.venv/bin/python ]; then
#    echo "Setting up virtual environment..."
#    uv sync --frozen
#fi

echo "Running database migrations..."
uv run python manage.py migrate --noinput

echo "Starting application..."
exec "$@"