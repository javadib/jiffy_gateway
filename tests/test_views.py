"""Tests for ingestion endpoints."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase

from apps.ingestion.views import GiteaIngestView, GitHubIngestView, GitLabIngestView
from jobs.models import Task


class TestGitHubIngest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.secret = "test-github-secret"
        self.payload = {
            "repo_url": "https://github.com/user/repo",
            "issue_external_id": "123",
            "thread_text": "Fix the bug in module X",
            "repo_token": "ghp_test_token",
            "callback_url": "https://example.com/callback",
            "callback_secret": "callback-secret-123",
        }

    def _sign_body(self, body: bytes) -> str:
        digest = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    @patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_valid_request_returns_202(self, mock_redis, mock_task):
        mock_redis.return_value = MagicMock(set=MagicMock(return_value=True))
        mock_task.delay.return_value = MagicMock(id="celery-123")

        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/github/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=self._sign_body(body),
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "queued")
        self.assertEqual(Task.objects.count(), 1)
        task = Task.objects.first()
        self.assertEqual(task.provider, "github")
        self.assertEqual(task.repo_url, self.payload["repo_url"])
        mock_task.delay.assert_called_once()

    @patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_invalid_signature_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/github/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=wrong",
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_missing_fields_returns_400(self, mock_redis, mock_task):
        incomplete_payload = {"repo_url": "https://github.com/user/repo"}
        body = json.dumps(incomplete_payload).encode()
        request = self.factory.post(
            "/api/ingest/github/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=self._sign_body(body),
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_duplicate_delivery_returns_202_no_new_task(self, mock_redis, mock_task):
        mock_redis_instance = MagicMock()
        mock_redis_instance.set.side_effect = [True, None, None]
        mock_redis.return_value = mock_redis_instance
        mock_task.delay.return_value = MagicMock(id="celery-123")

        body = json.dumps(self.payload).encode()
        request1 = self.factory.post(
            "/api/ingest/github/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=self._sign_body(body),
        )
        response1 = GitHubIngestView.as_view()(request1)
        self.assertEqual(response1.status_code, 202)

        request2 = self.factory.post(
            "/api/ingest/github/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=self._sign_body(body),
        )
        response2 = GitHubIngestView.as_view()(request2)
        self.assertEqual(response2.status_code, 202)
        self.assertEqual(Task.objects.count(), 1)


class TestGitLabIngest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.token = "test-gitlab-token"
        self.payload = {
            "repo_url": "https://gitlab.com/user/repo",
            "issue_external_id": "456",
            "thread_text": "Add feature Y",
            "repo_token": "glpat-test-token",
            "callback_url": "https://example.com/callback",
            "callback_secret": "callback-secret-456",
        }

    @patch.dict("os.environ", {"GITLAB_WEBHOOK_SECRET": "test-gitlab-token"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_valid_request_returns_202(self, mock_redis, mock_task):
        mock_redis.return_value = MagicMock(set=MagicMock(return_value=True))
        mock_task.delay.return_value = MagicMock(id="celery-456")

        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitlab/",
            data=body,
            content_type="application/json",
            HTTP_X_GITLAB_TOKEN=self.token,
        )

        response = GitLabIngestView.as_view()(request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(Task.objects.count(), 1)
        task = Task.objects.first()
        self.assertEqual(task.provider, "gitlab")

    @patch.dict("os.environ", {"GITLAB_WEBHOOK_SECRET": "test-gitlab-token"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_invalid_token_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitlab/",
            data=body,
            content_type="application/json",
            HTTP_X_GITLAB_TOKEN="wrong-token",
        )

        response = GitLabIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITLAB_WEBHOOK_SECRET": "test-gitlab-token"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_duplicate_delivery_returns_202(self, mock_redis, mock_task):
        mock_redis_instance = MagicMock()
        mock_redis_instance.set.side_effect = [True, None, None]
        mock_redis.return_value = mock_redis_instance
        mock_task.delay.return_value = MagicMock(id="celery-456")

        body = json.dumps(self.payload).encode()
        request1 = self.factory.post(
            "/api/ingest/gitlab/",
            data=body,
            content_type="application/json",
            HTTP_X_GITLAB_TOKEN=self.token,
        )
        GitLabIngestView.as_view()(request1)

        request2 = self.factory.post(
            "/api/ingest/gitlab/",
            data=body,
            content_type="application/json",
            HTTP_X_GITLAB_TOKEN=self.token,
        )
        response2 = GitLabIngestView.as_view()(request2)
        self.assertEqual(response2.status_code, 202)
        self.assertEqual(Task.objects.count(), 1)


class TestGiteaIngest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.secret = "test-gitea-secret"
        self.payload = {
            "repo_url": "https://gitea.com/user/repo",
            "issue_external_id": "789",
            "thread_text": "Refactor module Z",
            "repo_token": "gitea_test_token",
            "callback_url": "https://example.com/callback",
            "callback_secret": "callback-secret-789",
        }

    def _sign_body(self, body: bytes) -> str:
        return hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()

    @patch.dict("os.environ", {"GITEA_WEBHOOK_SECRET": "test-gitea-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_valid_request_returns_202(self, mock_redis, mock_task):
        mock_redis.return_value = MagicMock(set=MagicMock(return_value=True))
        mock_task.delay.return_value = MagicMock(id="celery-789")

        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitea/",
            data=body,
            content_type="application/json",
            HTTP_X_GITEA_SIGNATURE=self._sign_body(body),
        )

        response = GiteaIngestView.as_view()(request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(Task.objects.count(), 1)
        task = Task.objects.first()
        self.assertEqual(task.provider, "gitea")

    @patch.dict("os.environ", {"GITEA_WEBHOOK_SECRET": "test-gitea-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_invalid_signature_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitea/",
            data=body,
            content_type="application/json",
            HTTP_X_GITEA_SIGNATURE="wrong",
        )

        response = GiteaIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITEA_WEBHOOK_SECRET": "test-gitea-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_duplicate_delivery_returns_202(self, mock_redis, mock_task):
        mock_redis_instance = MagicMock()
        mock_redis_instance.set.side_effect = [True, None, None]
        mock_redis.return_value = mock_redis_instance
        mock_task.delay.return_value = MagicMock(id="celery-789")

        body = json.dumps(self.payload).encode()
        request1 = self.factory.post(
            "/api/ingest/gitea/",
            data=body,
            content_type="application/json",
            HTTP_X_GITEA_SIGNATURE=self._sign_body(body),
        )
        GiteaIngestView.as_view()(request1)

        request2 = self.factory.post(
            "/api/ingest/gitea/",
            data=body,
            content_type="application/json",
            HTTP_X_GITEA_SIGNATURE=self._sign_body(body),
        )
        response2 = GiteaIngestView.as_view()(request2)
        self.assertEqual(response2.status_code, 202)
        self.assertEqual(Task.objects.count(), 1)
