"""Tests for ingestion endpoints."""

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

    @patch.dict("os.environ", {"GITHUB_INGEST_TOKEN": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    @patch("apps.ingestion.views.transaction")
    def test_valid_request_returns_202(self, mock_transaction, mock_redis, mock_task):
        mock_redis.return_value = MagicMock(set=MagicMock(return_value=True))
        mock_task.apply_async.return_value = MagicMock(id="celery-123")
        mock_transaction.on_commit.side_effect = lambda cb: cb()
        mock_transaction.atomic.return_value.__enter__ = MagicMock()
        mock_transaction.atomic.return_value.__exit__ = MagicMock(return_value=False)

        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/github/?token=test-github-secret",
            data=body,
            content_type="application/json",
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "queued")
        self.assertEqual(Task.objects.count(), 1)
        task = Task.objects.first()
        self.assertEqual(task.provider, "github")
        self.assertEqual(task.repo_url, self.payload["repo_url"])
        mock_task.apply_async.assert_called_once()

    @patch.dict("os.environ", {"GITHUB_INGEST_TOKEN": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_invalid_token_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/github/?token=wrong",
            data=body,
            content_type="application/json",
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITHUB_INGEST_TOKEN": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_missing_token_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/github/",
            data=body,
            content_type="application/json",
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITHUB_INGEST_TOKEN": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_missing_fields_returns_400(self, mock_redis, mock_task):
        incomplete_payload = {"repo_url": "https://github.com/user/repo"}
        body = json.dumps(incomplete_payload).encode()
        request = self.factory.post(
            "/api/ingest/github/?token=test-github-secret",
            data=body,
            content_type="application/json",
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITHUB_INGEST_TOKEN": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_invalid_url_returns_400(self, mock_redis, mock_task):
        invalid_payload = self.payload.copy()
        invalid_payload["repo_url"] = "not-a-url"
        body = json.dumps(invalid_payload).encode()
        request = self.factory.post(
            "/api/ingest/github/?token=test-github-secret",
            data=body,
            content_type="application/json",
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITHUB_INGEST_TOKEN": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_non_dict_payload_returns_400(self, mock_redis, mock_task):
        body = json.dumps(["not", "a", "dict"]).encode()
        request = self.factory.post(
            "/api/ingest/github/?token=test-github-secret",
            data=body,
            content_type="application/json",
        )

        response = GitHubIngestView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITHUB_INGEST_TOKEN": "test-github-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    @patch("apps.ingestion.views.transaction")
    def test_duplicate_delivery_returns_202_no_new_task(self, mock_transaction, mock_redis, mock_task):
        mock_redis_instance = MagicMock()
        mock_redis_instance.set.side_effect = [True, None, None]
        mock_redis.return_value = mock_redis_instance
        mock_task.apply_async.return_value = MagicMock(id="celery-123")
        mock_transaction.on_commit.side_effect = lambda cb: cb()
        mock_transaction.atomic.return_value.__enter__ = MagicMock()
        mock_transaction.atomic.return_value.__exit__ = MagicMock(return_value=False)

        body = json.dumps(self.payload).encode()
        request1 = self.factory.post(
            "/api/ingest/github/?token=test-github-secret",
            data=body,
            content_type="application/json",
        )
        response1 = GitHubIngestView.as_view()(request1)
        self.assertEqual(response1.status_code, 202)

        request2 = self.factory.post(
            "/api/ingest/github/?token=test-github-secret",
            data=body,
            content_type="application/json",
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

    @patch.dict("os.environ", {"GITLAB_INGEST_TOKEN": "test-gitlab-token"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    @patch("apps.ingestion.views.transaction")
    def test_valid_request_returns_202(self, mock_transaction, mock_redis, mock_task):
        mock_redis.return_value = MagicMock(set=MagicMock(return_value=True))
        mock_task.apply_async.return_value = MagicMock(id="celery-456")
        mock_transaction.on_commit.side_effect = lambda cb: cb()
        mock_transaction.atomic.return_value.__enter__ = MagicMock()
        mock_transaction.atomic.return_value.__exit__ = MagicMock(return_value=False)

        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitlab/?token=test-gitlab-token",
            data=body,
            content_type="application/json",
        )

        response = GitLabIngestView.as_view()(request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(Task.objects.count(), 1)
        task = Task.objects.first()
        self.assertEqual(task.provider, "gitlab")

    @patch.dict("os.environ", {"GITLAB_INGEST_TOKEN": "test-gitlab-token"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_invalid_token_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitlab/?token=wrong-token",
            data=body,
            content_type="application/json",
        )

        response = GitLabIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITLAB_INGEST_TOKEN": "test-gitlab-token"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_missing_token_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitlab/",
            data=body,
            content_type="application/json",
        )

        response = GitLabIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITLAB_INGEST_TOKEN": "test-gitlab-token"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    @patch("apps.ingestion.views.transaction")
    def test_duplicate_delivery_returns_202(self, mock_transaction, mock_redis, mock_task):
        mock_redis_instance = MagicMock()
        mock_redis_instance.set.side_effect = [True, None, None]
        mock_redis.return_value = mock_redis_instance
        mock_task.apply_async.return_value = MagicMock(id="celery-456")
        mock_transaction.on_commit.side_effect = lambda cb: cb()
        mock_transaction.atomic.return_value.__enter__ = MagicMock()
        mock_transaction.atomic.return_value.__exit__ = MagicMock(return_value=False)

        body = json.dumps(self.payload).encode()
        request1 = self.factory.post(
            "/api/ingest/gitlab/?token=test-gitlab-token",
            data=body,
            content_type="application/json",
        )
        GitLabIngestView.as_view()(request1)

        request2 = self.factory.post(
            "/api/ingest/gitlab/?token=test-gitlab-token",
            data=body,
            content_type="application/json",
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

    @patch.dict("os.environ", {"GITEA_INGEST_TOKEN": "test-gitea-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    @patch("apps.ingestion.views.transaction")
    def test_valid_request_returns_202(self, mock_transaction, mock_redis, mock_task):
        mock_redis.return_value = MagicMock(set=MagicMock(return_value=True))
        mock_task.apply_async.return_value = MagicMock(id="celery-789")
        mock_transaction.on_commit.side_effect = lambda cb: cb()
        mock_transaction.atomic.return_value.__enter__ = MagicMock()
        mock_transaction.atomic.return_value.__exit__ = MagicMock(return_value=False)

        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitea/?token=test-gitea-secret",
            data=body,
            content_type="application/json",
        )

        response = GiteaIngestView.as_view()(request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(Task.objects.count(), 1)
        task = Task.objects.first()
        self.assertEqual(task.provider, "gitea")

    @patch.dict("os.environ", {"GITEA_INGEST_TOKEN": "test-gitea-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_invalid_token_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitea/?token=wrong",
            data=body,
            content_type="application/json",
        )

        response = GiteaIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITEA_INGEST_TOKEN": "test-gitea-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    def test_missing_token_returns_401(self, mock_redis, mock_task):
        body = json.dumps(self.payload).encode()
        request = self.factory.post(
            "/api/ingest/gitea/",
            data=body,
            content_type="application/json",
        )

        response = GiteaIngestView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    @patch.dict("os.environ", {"GITEA_INGEST_TOKEN": "test-gitea-secret"})
    @patch("apps.ingestion.views.execute_task")
    @patch("apps.ingestion.views.get_redis")
    @patch("apps.ingestion.views.transaction")
    def test_duplicate_delivery_returns_202(self, mock_transaction, mock_redis, mock_task):
        mock_redis_instance = MagicMock()
        mock_redis_instance.set.side_effect = [True, None, None]
        mock_redis.return_value = mock_redis_instance
        mock_task.apply_async.return_value = MagicMock(id="celery-789")
        mock_transaction.on_commit.side_effect = lambda cb: cb()
        mock_transaction.atomic.return_value.__enter__ = MagicMock()
        mock_transaction.atomic.return_value.__exit__ = MagicMock(return_value=False)

        body = json.dumps(self.payload).encode()
        request1 = self.factory.post(
            "/api/ingest/gitea/?token=test-gitea-secret",
            data=body,
            content_type="application/json",
        )
        GiteaIngestView.as_view()(request1)

        request2 = self.factory.post(
            "/api/ingest/gitea/?token=test-gitea-secret",
            data=body,
            content_type="application/json",
        )
        response2 = GiteaIngestView.as_view()(request2)
        self.assertEqual(response2.status_code, 202)
        self.assertEqual(Task.objects.count(), 1)
