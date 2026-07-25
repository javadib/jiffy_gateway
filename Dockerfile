FROM python:3.12-slim AS runner

ARG APP_VERSION=dev

WORKDIR /app

ENV APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && \
    uv export --frozen --no-dev --no-hashes -o requirements.txt && \
    uv pip install --system -r requirements.txt

RUN addgroup --system app && adduser --system --ingroup app app

COPY --chown=app:app . .

RUN python manage.py collectstatic --noinput

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

COPY --chown=app:app docker-entrypoint.sh /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
