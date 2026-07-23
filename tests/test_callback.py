"""Tests for callback dispatch."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.ingestion.callback import send_callback
from jobs.models import Task


class TestSendCallback(TestCase):
    """Tests for send_callback function."""

    def setUp(self):
        self.task = Task.objects.create(
            provider="github",
            repo_url="https://github.com/user/repo",
            issue_external_id="123",
            callback_url="https://example.com/callback",
            callback_secret="callback-secret-123",
            status="done",
        )

    @patch("apps.ingestion.callback.requests.post")
    def test_success_on_first_attempt(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_callback(self.task, status="done", summary="Task completed")

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertEqual(call_args[0][0], "https://example.com/callback")

        body = call_args[1]["data"]
        payload = json.loads(body)
        self.assertEqual(payload["task_id"], self.task.id)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["summary"], "Task completed")

        headers = call_args[1]["headers"]
        self.assertIn("X-Jiffy-Signature", headers)
        expected_sig = hmac.new(
            self.task.callback_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(headers["X-Jiffy-Signature"], expected_sig)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.post")
    def test_success_after_retries(self, mock_post, mock_sleep):
        fail_response = MagicMock()
        fail_response.status_code = 500
        success_response = MagicMock()
        success_response.status_code = 200
        mock_post.side_effect = [fail_response, success_response]

        send_callback(self.task, status="done")

        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.post")
    def test_all_retries_exhausted(self, mock_post, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        # Should not raise
        send_callback(self.task, status="failed", error_message="Error")

        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.post")
    def test_network_error_retries(self, mock_post, mock_sleep):
        import requests

        mock_post.side_effect = [
            requests.ConnectionError("Connection refused"),
            requests.ConnectionError("Timeout"),
            MagicMock(status_code=200),
        ]

        send_callback(self.task, status="done")

        self.assertEqual(mock_post.call_count, 3)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.post")
    def test_includes_optional_fields(self, mock_post, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_callback(
            self.task,
            status="done",
            summary="Summary",
            pr_url="https://github.com/user/repo/pull/1",
        )

        body = mock_post.call_args[1]["data"]
        payload = json.loads(body)
        self.assertEqual(payload["pr_url"], "https://github.com/user/repo/pull/1")
        self.assertEqual(payload["summary"], "Summary")

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.post")
    def test_omits_none_fields(self, mock_post, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_callback(self.task, status="done")

        body = mock_post.call_args[1]["data"]
        payload = json.loads(body)
        self.assertNotIn("summary", payload)
        self.assertNotIn("pr_url", payload)
        self.assertNotIn("error_message", payload)
