"""Tests for callback dispatch."""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.ingestion.callback import send_fallback_callback, send_callback
from jobs.callback_specs import get_callback_spec
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

    @patch("apps.ingestion.callback.requests.request")
    def test_success_on_first_attempt(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(self.task, status="done", summary="Task completed")

        mock_request.assert_called_once()
        call_args = mock_request.call_args
        self.assertEqual(call_args[0][0], "POST")
        self.assertEqual(call_args[1]["url"], "https://example.com/callback")

        body = call_args[1]["data"]
        payload = json.loads(body)
        self.assertEqual(payload["task_id"], self.task.id)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["summary"], "Task completed")

        headers = call_args[1]["headers"]
        self.assertNotIn("X-Jiffy-Signature", headers)

    @patch("apps.ingestion.callback.requests.request")
    def test_sends_raw_secret_in_authorization_header(self, mock_request):
        """The callback secret must be sent byte-for-byte unchanged in the Authorization header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(self.task, status="done")

        headers = mock_request.call_args[1]["headers"]
        self.assertEqual(
            headers["Authorization"],
            "Bearer " + self.task.callback_secret,
        )
        self.assertIn("Bearer callback-secret-123", headers["Authorization"])

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_success_after_retries(self, mock_request, mock_sleep):
        fail_response = MagicMock()
        fail_response.status_code = 500
        success_response = MagicMock()
        success_response.status_code = 200
        mock_request.side_effect = [fail_response, success_response]

        send_callback(self.task, status="done")

        self.assertEqual(mock_request.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_all_retries_exhausted(self, mock_request, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_request.return_value = mock_response

        # Should not raise
        send_callback(self.task, status="failed", error_message="Error")

        self.assertEqual(mock_request.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_network_error_retries(self, mock_request, mock_sleep):
        import requests

        mock_request.side_effect = [
            requests.ConnectionError("Connection refused"),
            requests.ConnectionError("Timeout"),
            MagicMock(status_code=200),
        ]

        send_callback(self.task, status="done")

        self.assertEqual(mock_request.call_count, 3)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_includes_optional_fields(self, mock_request, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(
            self.task,
            status="done",
            summary="Summary",
            pr_url="https://github.com/user/repo/pull/1",
        )

        body = mock_request.call_args[1]["data"]
        payload = json.loads(body)
        self.assertEqual(payload["pr_url"], "https://github.com/user/repo/pull/1")
        self.assertEqual(payload["summary"], "Summary")

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_omits_none_fields(self, mock_request, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(self.task, status="done")

        body = mock_request.call_args[1]["data"]
        payload = json.loads(body)
        self.assertNotIn("summary", payload)
        self.assertNotIn("pr_url", payload)
        self.assertNotIn("error_message", payload)


class TestSendFallbackCallback(TestCase):
    """Tests for the Gateway fallback callback path."""

    def setUp(self):
        self.task = Task.objects.create(
            provider="github",
            repo_url="https://github.com/user/repo",
            issue_external_id="123",
            callback_url="https://example.com/callback",
            callback_secret="fallback-secret",
            status="failed",
            branch_name="Jiffy/attempt",
            error_message="Agent crashed",
        )

    @patch("apps.ingestion.callback.requests.request")
    def test_fallback_sends_callback(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_fallback_callback(
            self.task,
            status="failed",
            summary="Agent failed",
            branch_name="Jiffy/attempt",
            error_message="Agent crashed",
        )

        mock_request.assert_called_once()
        call_args = mock_request.call_args
        self.assertEqual(call_args[0][0], "POST")

        headers = call_args[1]["headers"]
        self.assertIn("Bearer fallback-secret", headers["Authorization"])

        body = call_args[1]["data"]
        payload = json.loads(body)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["branch_name"], "Jiffy/attempt")
        self.assertEqual(payload["error_message"], "Agent crashed")

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_fallback_retries_on_failure(self, mock_request, mock_sleep):
        mock_request.side_effect = [
            MagicMock(status_code=500),
            MagicMock(status_code=500),
            MagicMock(status_code=200),
        ]

        send_fallback_callback(self.task, status="failed", error_message="err")

        self.assertEqual(mock_request.call_count, 3)

    @patch("apps.ingestion.callback.requests.request")
    def test_fallback_all_retries_exhausted(self, mock_request):
        mock_request.return_value = MagicMock(status_code=500)

        send_fallback_callback(self.task, status="failed", error_message="err")

        self.assertEqual(mock_request.call_count, 3)
