"""Tests for callback dispatch."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.ingestion.callback import format_callback_body, send_fallback_callback, send_callback
from jobs.models import Task


class TestFormatCallbackBody(TestCase):
    """Tests for format_callback_body function."""

    def test_success_with_all_fields(self):
        result = format_callback_body(
            task_id=42,
            status="done",
            summary="Fixed the bug.",
            branch_name="fix-bug",
            pr_url="https://github.com/user/repo/pull/1",
        )
        self.assertIn("Task #42: ✅ Jiffy completed this task.", result)
        self.assertIn("**Summary:** Fixed the bug.", result)
        self.assertIn("**Branch:** fix-bug", result)
        self.assertIn("**Pull Request:** https://github.com/user/repo/pull/1", result)

    def test_success_without_pr_url(self):
        result = format_callback_body(
            task_id=42,
            status="done",
            summary="Fixed the bug.",
            branch_name="fix-bug",
        )
        self.assertIn("**Summary:** Fixed the bug.", result)
        self.assertIn("**Branch:** fix-bug", result)
        self.assertNotIn("Pull Request", result)

    def test_success_without_branch(self):
        result = format_callback_body(
            task_id=42,
            status="done",
            summary="Fixed the bug.",
            pr_url="https://github.com/user/repo/pull/1",
        )
        self.assertIn("**Summary:** Fixed the bug.", result)
        self.assertIn("**Pull Request:** https://github.com/user/repo/pull/1", result)
        self.assertNotIn("**Branch:**", result)

    def test_success_minimal(self):
        result = format_callback_body(task_id=42, status="done")
        self.assertIn("Task #42: ✅ Jiffy completed this task.", result)
        self.assertNotIn("**Summary:**", result)
        self.assertNotIn("**Branch:**", result)
        self.assertNotIn("Pull Request", result)

    def test_failed_with_error(self):
        result = format_callback_body(
            task_id=42, status="failed", error_message="Something went wrong."
        )
        self.assertIn("Task #42: ❌ Jiffy could not complete this task.", result)
        self.assertIn("**Reason:** Something went wrong.", result)

    def test_failed_without_error(self):
        result = format_callback_body(task_id=42, status="failed")
        self.assertIn("Task #42: ❌ Jiffy could not complete this task.", result)
        self.assertNotIn("**Reason:**", result)

    def test_success_with_technical_report(self):
        """technical_report is included with a separator and heading."""
        result = format_callback_body(
            task_id=42,
            status="done",
            summary="Fixed the bug.",
            technical_report="## What was done\nFixed the bug.\n\n## Reasoning\nBecause Y.",
        )
        self.assertIn("---", result)
        self.assertIn("### Technical Report", result)
        self.assertIn("## What was done", result)
        self.assertIn("Fixed the bug.", result)
        self.assertIn("## Reasoning", result)
        self.assertIn("Because Y.", result)

    def test_technical_report_omitted_when_empty(self):
        """When technical_report is empty/None, omit the section entirely."""
        result = format_callback_body(
            task_id=42,
            status="done",
            summary="Fixed the bug.",
        )
        self.assertNotIn("Technical Report", result)
        self.assertNotIn("---", result)

    def test_technical_report_omitted_when_empty_string(self):
        """When technical_report is empty string, omit the section entirely."""
        result = format_callback_body(
            task_id=42,
            status="done",
            summary="Fixed the bug.",
            technical_report="",
        )
        self.assertNotIn("Technical Report", result)
        self.assertNotIn("---", result)

    def test_technical_report_not_in_failed(self):
        """technical_report should not appear in failed callback body."""
        result = format_callback_body(
            task_id=42,
            status="failed",
            error_message="Broke.",
            technical_report="## What was done",
        )
        self.assertNotIn("Technical Report", result)


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
        self.assertEqual(call_args[0][1], "https://example.com/callback")

        body = call_args[1]["data"].decode("utf-8")
        self.assertIn("✅ Jiffy completed this task.", body)
        self.assertIn("**Summary:** Task completed", body)
        self.assertIn(f"Task #{self.task.id}", body)

        headers = call_args[1]["headers"]
        self.assertIn("Content-Type", headers)

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
            technical_report="## What was done\nDetailed report.",
            pr_url="https://github.com/user/repo/pull/1",
        )

        body = mock_request.call_args[1]["data"].decode("utf-8")
        self.assertIn("**Summary:** Summary", body)
        self.assertIn("**Pull Request:** https://github.com/user/repo/pull/1", body)
        self.assertIn("### Technical Report", body)
        self.assertIn("Detailed report.", body)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_failed_callback_format(self, mock_request, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(self.task, status="failed", error_message="Something broke")

        body = mock_request.call_args[1]["data"].decode("utf-8")
        self.assertIn("❌ Jiffy could not complete this task.", body)
        self.assertIn("**Reason:** Something broke", body)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_github_sends_json_comment_body(self, mock_request, mock_sleep):
        """GitHub's Issue Comments API takes JSON {"body": ...}, not text/plain."""
        import json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(self.task, status="done", summary="Task completed")

        headers = mock_request.call_args[1]["headers"]
        self.assertEqual(headers["Content-Type"], "application/json")

        payload = json.loads(mock_request.call_args[1]["data"].decode("utf-8"))
        self.assertEqual(list(payload), ["body"])
        self.assertIn("**Summary:** Task completed", payload["body"])

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_github_sends_api_version_and_accept_headers(self, mock_request, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(self.task, status="done")

        headers = mock_request.call_args[1]["headers"]
        self.assertEqual(headers["Accept"], "application/vnd.github+json")
        self.assertEqual(headers["X-GitHub-Api-Version"], "2026-03-10")

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_gitea_still_sends_plain_text_body(self, mock_request, mock_sleep):
        """Non-GitHub providers keep their existing plain-text behavior."""
        self.task.provider = "gitea"
        self.task.save()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(self.task, status="done", summary="Task completed")

        headers = mock_request.call_args[1]["headers"]
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertNotIn("X-GitHub-Api-Version", headers)
        self.assertTrue(
            mock_request.call_args[1]["data"].decode("utf-8").startswith("Task #")
        )

    @patch("apps.ingestion.callback.requests.request")
    def test_send_callback_with_branch_name(self, mock_request):
        self.task.branch_name = "fix-bug"
        self.task.save()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(self.task, status="done", summary="Fixed it.")

        body = mock_request.call_args[1]["data"].decode("utf-8")
        self.assertIn("**Branch:** fix-bug", body)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_technical_report_in_callback_payload(self, mock_request, mock_sleep):
        """technical_report must be passed through to the callback body."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_callback(
            self.task,
            status="done",
            summary="Summary",
            technical_report="## Reasoning\nBecause Z.",
        )

        body = mock_request.call_args[1]["data"].decode("utf-8")
        self.assertIn("### Technical Report", body)
        self.assertIn("## Reasoning", body)
        self.assertIn("Because Z.", body)

    @patch("apps.ingestion.callback.requests.request")
    def test_returns_true_on_success(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200)

        result = send_callback(self.task, status="done")

        self.assertTrue(result)

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_returns_false_when_retries_exhausted(self, mock_request, mock_sleep):
        mock_request.return_value = MagicMock(status_code=500)

        result = send_callback(self.task, status="failed", error_message="Error")

        self.assertFalse(result)

    def test_returns_false_for_unknown_provider(self):
        self.task.provider = "bitbucket"
        self.task.save()

        result = send_callback(self.task, status="done")

        self.assertFalse(result)


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

        body = call_args[1]["data"].decode("utf-8")
        self.assertIn("❌ Jiffy could not complete this task.", body)
        self.assertIn("**Reason:** Agent crashed", body)

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

    @patch("apps.ingestion.callback.time.sleep")
    @patch("apps.ingestion.callback.requests.request")
    def test_fallback_with_technical_report(self, mock_request, mock_sleep):
        """Fallback callback includes technical_report when provided."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        send_fallback_callback(
            self.task,
            status="done",
            summary="Done.",
            technical_report="## What was done\nTask completed.",
            branch_name="fix-bug",
            pr_url="https://example.com/pr/1",
        )

        body = mock_request.call_args[1]["data"].decode("utf-8")
        self.assertIn("### Technical Report", body)
        self.assertIn("Task completed.", body)

    @patch("apps.ingestion.callback.requests.request")
    def test_returns_true_on_success(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200)

        result = send_fallback_callback(self.task, status="failed", error_message="err")

        self.assertTrue(result)

    @patch("apps.ingestion.callback.requests.request")
    def test_returns_false_when_retries_exhausted(self, mock_request):
        mock_request.return_value = MagicMock(status_code=500)

        result = send_fallback_callback(self.task, status="failed", error_message="err")

        self.assertFalse(result)

    def test_returns_false_for_unknown_provider(self):
        self.task.provider = "bitbucket"
        self.task.save()

        result = send_fallback_callback(self.task, status="failed", error_message="err")

        self.assertFalse(result)
