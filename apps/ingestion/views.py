"""Ingestion endpoints for GitHub, GitLab, and Gitea webhooks."""

import json
import logging

import redis
from django.conf import settings
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ingestion.auth import verify_gitea_signature, verify_github_signature, verify_gitlab_token
from apps.ingestion.serializers import IngestionPayloadSerializer
from apps.ingestion.tasks import execute_task
from jobs.models import Task

logger = logging.getLogger(__name__)

_redis_client = None


def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


PAYLOAD_TTL_SECONDS = 4 * 60 * 60
LOCK_TTL_SECONDS = 300


def _acquire_lock(lock_key: str) -> bool:
    r = get_redis()
    return r.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)


def _store_payload(task_id: int, payload: dict) -> None:
    r = get_redis()
    key = f"jiffy:task:{task_id}:payload"
    r.set(key, json.dumps(payload), ex=PAYLOAD_TTL_SECONDS)


def _handle_ingestion(provider: str, data: dict) -> Response:
    repo_url = data.get("repo_url")
    issue_external_id = data.get("issue_external_id")
    thread_text = data.get("thread_text")
    repo_token = data.get("repo_token")
    callback_url = data.get("callback_url")
    callback_secret = data.get("callback_secret")

    missing = []
    if not repo_url:
        missing.append("repo_url")
    if not issue_external_id:
        missing.append("issue_external_id")
    if not thread_text:
        missing.append("thread_text")
    if not repo_token:
        missing.append("repo_token")
    if not callback_url:
        missing.append("callback_url")
    if not callback_secret:
        missing.append("callback_secret")

    if missing:
        return Response(
            {"error": f"Missing required fields: {', '.join(missing)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    lock_key = f"jiffy:lock:issue:{provider}:{issue_external_id}"
    if not _acquire_lock(lock_key):
        logger.info(
            "Duplicate delivery for %s issue %s — skipping",
            provider,
            issue_external_id,
        )
        return Response({"status": "already_queued"}, status=status.HTTP_202_ACCEPTED)

    task = Task.objects.create(
        provider=provider,
        repo_url=repo_url,
        issue_external_id=issue_external_id,
        callback_url=callback_url,
        callback_secret=callback_secret,
        status="queued",
    )

    payload = {"thread_text": thread_text, "repo_token": repo_token}
    _store_payload(task.id, payload)

    result = execute_task.delay(task.id)
    task.celery_task_id = result.id
    task.save(update_fields=["celery_task_id", "updated_at"])

    logger.info(
        "Ingested %s issue %s as task %d (celery=%s)",
        provider,
        issue_external_id,
        task.id,
        result.id,
    )

    return Response({"task_id": task.id, "status": "queued"}, status=status.HTTP_202_ACCEPTED)


class GitHubIngestView(APIView):
    """Ingest webhook from GitHub.

    Auth: X-Hub-Signature-256 header with HMAC-SHA256(secret, body).
    """

    authentication_classes = []
    permission_classes = []
    serializer_class = IngestionPayloadSerializer

    def post(self, request: Request) -> Response:
        raw_body = request.body
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_github_signature(raw_body, signature):
            return Response({"error": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            data = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)
        return _handle_ingestion("github", data)


class GitLabIngestView(APIView):
    """Ingest webhook from GitLab.

    Auth: X-Gitlab-Token header with shared token.
    """

    authentication_classes = []
    permission_classes = []
    serializer_class = IngestionPayloadSerializer

    def post(self, request: Request) -> Response:
        token = request.headers.get("X-Gitlab-Token", "")
        if not verify_gitlab_token(token):
            return Response({"error": "Invalid token"}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)
        return _handle_ingestion("gitlab", data)


class GiteaIngestView(APIView):
    """Ingest webhook from Gitea.

    Auth: X-Gitea-Signature header with HMAC-SHA256(secret, body).
    """

    authentication_classes = []
    permission_classes = []
    serializer_class = IngestionPayloadSerializer

    def post(self, request: Request) -> Response:
        raw_body = request.body
        signature = request.headers.get("X-Gitea-Signature", "")
        if not verify_gitea_signature(raw_body, signature):
            return Response({"error": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            data = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)
        return _handle_ingestion("gitea", data)
