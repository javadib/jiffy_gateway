#!/bin/sh
set -e

# Ensure data directory exists and is writable (volume mount may bring root-owned dir)
mkdir -p /app/data
chmod -R 777 /app/data

echo "Running database migrations..."
python manage.py migrate --noinput

echo "Starting application..."
exec "$@"
