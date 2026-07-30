"""Tests for the execution pipeline: agent instructions, result parsing, task orchestration,
sandbox image management, and logging."""

import json
import logging
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from jobs.execution.agent import AgentResult, build_agent_instructions, read_agent_result, _extract_issue_text, _format_turns
from jobs.execution.container import (
    _extract_git_host,
    _inject_token_into_url,
    _redact_url,
    ensure_sandbox_image,
    get_docker_client,
)
from jobs.execution.exceptions import ContainerError
from jobs.models import Task


# ---------------------------------------------------------------------------
# build_agent_instructions
# ---------------------------------------------------------------------------


class FormatTurnsTest(TestCase):
    """Unit tests for _format_turns and _extract_issue_text."""

    def test_format_turns_basic(self):
        turns = [
            {"role": "user", "author": "alice", "body": "Hello", "created_at": "2025-01-01T00:00:00Z"},
            {"role": "agent", "author": "jiffy-bot", "body": "Hi there!", "created_at": "2025-01-01T01:00:00Z"},
        ]
        result = _format_turns(turns)
        self.assertIn("--- Turn: User (alice) ---", result)
        self.assertIn("Hello", result)
        self.assertIn("--- Turn: Agent (Jiffy) (jiffy-bot) ---", result)
        self.assertIn("Hi there!", result)

    def test_format_turns_empty_body(self):
        turns = [
            {"role": "user", "author": "bob", "body": "", "created_at": "2025-01-01T00:00:00Z"},
        ]
        result = _format_turns(turns)
        self.assertIn("bob", result)

    def test_extract_issue_text_prefers_turns(self):
        payload = {
            "issue": {
                "turns": [
                    {"role": "user", "author": "alice", "body": "From turns", "created_at": "2025-01-01T00:00:00Z"},
                ],
                "text": "From legacy text",
            }
        }
        result = _extract_issue_text(payload)
        self.assertIn("From turns", result)
        self.assertNotIn("From legacy text", result)

    def test_extract_issue_text_falls_back_to_text(self):
        payload = {
            "issue": {
                "text": "Legacy text fallback",
            }
        }
        result = _extract_issue_text(payload)
        self.assertEqual(result, "Legacy text fallback")

    def test_extract_issue_text_empty_turns_list(self):
        payload = {
            "issue": {
                "turns": [],
                "text": "Fallback when turns empty",
            }
        }
        result = _extract_issue_text(payload)
        self.assertEqual(result, "Fallback when turns empty")

    def test_extract_issue_text_no_issue_at_all(self):
        result = _extract_issue_text({})
        self.assertEqual(result, "")

    def test_extract_issue_text_turns_not_a_list(self):
        payload = {"issue": {"turns": "not_a_list"}}
        result = _extract_issue_text(payload)
        self.assertEqual(result, "")


class BuildAgentInstructionsTest(TestCase):
    """Unit tests for build_agent_instructions."""

    def _make_payload(self, issue_text="Fix the bug", extra=None):
        payload = {
            "issue": {"text": issue_text, "external_issue_id": "123"},
            "repo": {"url": "https://github.com/user/repo", "token": "tok"},
            "callback": {"url": "https://example.com/cb", "secret": "s"},
        }
        if extra:
            payload.update(extra)
        return payload

    def test_includes_raw_issue_text(self):
        text = "This is the exact issue text — no summarization."
        instructions = build_agent_instructions(self._make_payload(issue_text=text))
        self.assertIn(text, instructions)

    def test_issue_text_not_modified(self):
        """Whitespace, special characters, and casing must pass through unchanged."""
        text = "  Line one\nLine TWO\n\ttabbed  "
        instructions = build_agent_instructions(self._make_payload(issue_text=text))
        self.assertIn(text, instructions)

    def test_includes_source_path(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("/workspace", instructions)

    def test_includes_output_contract(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn(".jiffy_result.json", instructions)
        self.assertIn("status", instructions)
        self.assertIn("branch_name", instructions)
        self.assertIn("summary", instructions)
        self.assertIn("technical_report", instructions)

    def test_mentions_branch_fallback(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("Jiffy/", instructions)

    def test_mentions_pr_only_if_asked(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("only if the issue text", instructions.lower())

    def test_mentions_code_review_only_if_asked(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("code review", instructions.lower())

    def test_empty_issue_text(self):
        instructions = build_agent_instructions(self._make_payload(issue_text=""))
        self.assertIn("/workspace", instructions)
        self.assertIn(".jiffy_result.json", instructions)

    def test_includes_callback_spec(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("Callback Delivery", instructions)
        self.assertIn("- **URL**:", instructions)
        self.assertIn("attempted", instructions)
        self.assertIn("succeeded", instructions)

    def test_includes_callback_url_and_secret(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("https://example.com/cb", instructions)

    def test_callback_field_in_output_contract(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("callback", instructions)
        self.assertIn("attempted", instructions)
        self.assertIn("succeeded", instructions)

    def test_includes_technical_report_in_output_contract(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("technical_report", instructions)

    def test_mentions_technical_report_structure(self):
        instructions = build_agent_instructions(self._make_payload())
        self.assertIn("Technical Report", instructions)
        self.assertIn("What was done", instructions)
        self.assertIn("Technology / approach chosen", instructions)
        self.assertIn("Reasoning", instructions)
        self.assertIn("Known limitations / follow-ups", instructions)

    def test_turns_preferred_over_text(self):
        payload = {
            "issue": {
                "turns": [
                    {"role": "user", "author": "alice", "body": "Fix the bug", "created_at": "2025-01-01T00:00:00Z"},
                    {"role": "agent", "author": "jiffy-bot", "body": "On it!", "created_at": "2025-01-01T01:00:00Z"},
                ],
            },
            "repo": {"url": "https://github.com/user/repo", "token": "tok"},
            "callback": {"url": "https://example.com/cb", "secret": "s"},
        }
        instructions = build_agent_instructions(payload)
        self.assertIn("Fix the bug", instructions)
        self.assertIn("On it!", instructions)
        self.assertIn("User (alice)", instructions)
        self.assertIn("Agent (Jiffy) (jiffy-bot)", instructions)

    def test_turns_missing_falls_back_to_text(self):
        payload = {
            "issue": {"text": "Legacy fallback text"},
            "repo": {"url": "https://github.com/user/repo", "token": "tok"},
            "callback": {"url": "https://example.com/cb", "secret": "s"},
        }
        instructions = build_agent_instructions(payload)
        self.assertIn("Legacy fallback text", instructions)


# ---------------------------------------------------------------------------
# read_agent_result
# ---------------------------------------------------------------------------


class ReadAgentResultTest(TestCase):
    """Unit tests for read_agent_result."""

    def _make_container(self, exit_code=0, output=None):
        container = MagicMock()
        container.short_id = "abc123"
        container.exec_run.return_value = (exit_code, (output, b""))
        return container

    def test_valid_done_result(self):
        result_data = {
            "status": "done",
            "branch_name": "Jiffy/fix-bug",
            "pr_url": "https://github.com/user/repo/pull/42",
            "programming_language": "python",
            "summary": "Fixed the bug.",
            "technical_report": "## What was done\nFixed the bug.",
            "error_message": None,
            "callback": {"attempted": True, "succeeded": True, "error": None},
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "done")
        self.assertEqual(result.branch_name, "Jiffy/fix-bug")
        self.assertEqual(result.pr_url, "https://github.com/user/repo/pull/42")
        self.assertEqual(result.programming_language, "python")
        self.assertEqual(result.summary, "Fixed the bug.")
        self.assertEqual(result.technical_report, "## What was done\nFixed the bug.")
        self.assertIsNone(result.error_message)
        self.assertEqual(result.callback, {"attempted": True, "succeeded": True, "error": None})

    def test_valid_failed_result(self):
        result_data = {
            "status": "failed",
            "branch_name": "Jiffy/attempt",
            "pr_url": None,
            "programming_language": None,
            "summary": None,
            "technical_report": None,
            "error_message": "Could not install dependency X.",
            "callback": {"attempted": True, "succeeded": False, "error": "HTTP 500"},
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_message, "Could not install dependency X.")
        self.assertEqual(result.callback, {"attempted": True, "succeeded": False, "error": "HTTP 500"})
        self.assertIsNone(result.technical_report)

    def test_missing_result_file(self):
        container = self._make_container(exit_code=1, output=None)
        result = read_agent_result(container)

        self.assertEqual(result.status, "failed")
        self.assertIn("not found", result.error_message.lower())

    def test_malformed_json(self):
        container = self._make_container(output=b"not json at all")
        result = read_agent_result(container)

        self.assertEqual(result.status, "failed")
        self.assertIn("not valid JSON", result.error_message)

    def test_missing_status_field(self):
        container = self._make_container(output=b'{"branch_name": "x"}')
        result = read_agent_result(container)

        self.assertEqual(result.status, "failed")
        self.assertIn("missing a valid 'status' field", result.error_message)

    def test_empty_json_object(self):
        container = self._make_container(output=b"{}")
        result = read_agent_result(container)

        self.assertEqual(result.status, "failed")
        self.assertIn("missing a valid 'status' field", result.error_message)

    def test_container_exec_run_exception(self):
        container = MagicMock()
        container.short_id = "err1"
        container.exec_run.side_effect = RuntimeError("connection lost")
        result = read_agent_result(container)

        self.assertEqual(result.status, "failed")
        self.assertIn("connection lost", result.error_message)

    def test_default_callback_when_missing(self):
        """When the agent result has no callback field, the default is not-attempted."""
        result_data = {
            "status": "done",
            "branch_name": "Jiffy/fix",
            "pr_url": None,
            "programming_language": None,
            "summary": "Worked.",
            "error_message": None,
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "done")
        self.assertIsInstance(result.callback, dict)
        self.assertFalse(result.callback["attempted"])
        self.assertFalse(result.callback["succeeded"])
        self.assertIn("Missing or invalid", result.callback["error"])

    def test_callback_invalid_type(self):
        """A non-dict callback field should produce a default not-attempted."""
        result_data = {
            "status": "done",
            "branch_name": "Jiffy/fix",
            "pr_url": None,
            "programming_language": None,
            "summary": "Worked.",
            "error_message": None,
            "callback": "not_a_dict",
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "done")
        self.assertFalse(result.callback["attempted"])
        self.assertFalse(result.callback["succeeded"])

    def test_callback_attempted_but_failed(self):
        result_data = {
            "status": "done",
            "branch_name": "Jiffy/fix",
            "pr_url": None,
            "programming_language": None,
            "summary": "Worked.",
            "error_message": None,
            "callback": {"attempted": True, "succeeded": False, "error": "Connection refused"},
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "done")
        self.assertTrue(result.callback["attempted"])
        self.assertFalse(result.callback["succeeded"])
        self.assertEqual(result.callback["error"], "Connection refused")

    def test_technical_report_parsed_when_present(self):
        """technical_report is correctly parsed from agent JSON."""
        result_data = {
            "status": "done",
            "branch_name": "Jiffy/fix",
            "pr_url": None,
            "programming_language": "python",
            "summary": "Fixed the bug.",
            "technical_report": "## What was done\nChanged X.\n\n## Reasoning\nBecause Y.",
            "error_message": None,
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "done")
        self.assertEqual(result.technical_report, "## What was done\nChanged X.\n\n## Reasoning\nBecause Y.")

    def test_technical_report_missing_returns_none(self):
        """If technical_report is missing from JSON, it should be None."""
        result_data = {
            "status": "done",
            "branch_name": "Jiffy/fix",
            "pr_url": None,
            "programming_language": "python",
            "summary": "Fixed the bug.",
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "done")
        self.assertIsNone(result.technical_report)


# ---------------------------------------------------------------------------
# execute_task orchestration (all Docker/agent calls mocked)
# ---------------------------------------------------------------------------


class ExecuteTaskTest(TestCase):
    """Integration-style tests for execute_task with mocked Docker calls."""

    def _create_task(self, **kwargs):
        defaults = {
            "provider": "github",
            "repo_url": "https://github.com/user/repo",
            "issue_external_id": "100",
            "callback_url": "https://example.com/cb",
            "callback_secret": "sec",
            "status": "queued",
        }
        defaults.update(kwargs)
        return Task.objects.create(**defaults)

    def _make_payload(self, **overrides):
        payload = {
            "repo": {"url": "https://github.com/user/repo", "token": "ghp_test"},
            "issue": {"text": "Fix the thing", "external_issue_id": "100"},
            "callback": {"url": "https://example.com/cb", "secret": "sec"},
        }
        payload.update(overrides)
        return payload

    def _make_agent_result(self, **overrides):
        result = {
            "status": "done",
            "branch_name": "Jiffy/fix-thing",
            "pr_url": "https://github.com/user/repo/pull/1",
            "programming_language": "python",
            "summary": "Fixed the thing.",
            "technical_report": "## What was done\nFixed the thing.",
            "error_message": None,
            "model": None,
            "callback": {"attempted": True, "succeeded": True, "error": None},
        }
        result.update(overrides)
        return AgentResult(**{k: v for k, v in result.items() if k in AgentResult._fields})

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_happy_path_agent_callback_success(
        self, mock_load, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
    ):
        """Agent succeeds and delivers callback — Gateway skips own callback."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = self._make_agent_result()

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "done")
        self.assertEqual(task.branch_name, "Jiffy/fix-thing")
        self.assertEqual(task.pr_url, "https://github.com/user/repo/pull/1")
        self.assertEqual(task.programming_language, "python")
        mock_cb.assert_not_called()

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_agent_callback_failed_gateway_falls_back(
        self, mock_load, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
    ):
        """Agent attempts callback but fails — Gateway falls back via spec."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = self._make_agent_result(
            callback={"attempted": True, "succeeded": False, "error": "HTTP 500"}
        )

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "done")
        mock_cb.assert_called_once()

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_agent_no_callback_attempt_gateway_falls_back(
        self, mock_load, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
    ):
        """Agent never attempts callback — Gateway falls back via spec."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = self._make_agent_result(
            callback={"attempted": False, "succeeded": False, "error": "Network unavailable"}
        )

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "done")
        mock_cb.assert_called_once()

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_agent_crashed_no_result_gateway_falls_back(
        self, mock_load, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
    ):
        """Agent crashes with no result — Gateway sends fallback with generic failure."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(
            status="failed",
            branch_name=None,
            pr_url=None,
            programming_language=None,
            summary=None,
            technical_report=None,
            error_message="Agent result file not found. The agent did not produce the required output contract.",
            model=None,
            callback={"attempted": False, "succeeded": False, "error": "No callback information available"},
        )

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        mock_cb.assert_called_once()

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_failed_agent_result_short_circuits(
        self, mock_load, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
    ):
        """A failed agent result must NOT update branch_name, pr_url, or programming_language."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(
            status="failed",
            branch_name="Jiffy/attempt",
            pr_url="https://example.com/pr",
            programming_language="python",
            summary=None,
            technical_report=None,
            error_message="Agent failed.",
            model=None,
            callback={"attempted": True, "succeeded": False, "error": "HTTP 500"},
        )

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.error_message, "Agent failed.")
        self.assertIsNone(task.branch_name)
        self.assertIsNone(task.pr_url)
        self.assertIsNone(task.programming_language)
        mock_cb.assert_called_once()

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_missing_payload_fails_task(self, mock_load, mock_cb):
        task = self._create_task()
        mock_load.side_effect = ValueError("Payload expired")

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertIn("Payload expired", task.error_message)

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_clone_failure_fails_task(
        self, mock_load, mock_container, mock_clone, mock_ensure, mock_cb
    ):
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_clone.side_effect = ContainerError("git clone failed")

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertIn("git clone failed", task.error_message)

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_nonexistent_task_does_not_crash(self, mock_load, mock_cb):
        mock_load.return_value = self._make_payload()

        from jobs.tasks import execute_task

        execute_task(99999)

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_status_transitions(
        self, mock_load, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
    ):
        """Verify the full status transition sequence on success."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()

        status_log = []

        original_save = Task.save

        def tracking_save(self_task, *args, **kwargs):
            status_log.append(self_task.status)
            original_save(self_task, *args, **kwargs)

        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = self._make_agent_result()

        from jobs.tasks import execute_task

        with patch.object(Task, "save", tracking_save):
            execute_task(task.id)

        self.assertIn("provisioning", status_log)
        self.assertIn("cloning", status_log)
        self.assertIn("running", status_log)
        self.assertIn("done", status_log)

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_agent_failure_with_callback_attempt(
        self, mock_load, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
    ):
        """Agent fails but attempted callback — Gateway still falls back."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(
            status="failed",
            branch_name="Jiffy/attempt",
            pr_url=None,
            programming_language=None,
            summary=None,
            technical_report=None,
            error_message="Could not install dependency.",
            model=None,
            callback={"attempted": True, "succeeded": False, "error": "HTTP 500"},
        )

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertIn("Could not install dependency.", task.error_message)
        mock_cb.assert_called_once()


# ---------------------------------------------------------------------------
# execute_task logging
# ---------------------------------------------------------------------------


class ExecuteTaskLoggingTest(TestCase):
    """Verify that execute_task emits structured, ordered log lines."""

    def _create_task(self, **kwargs):
        defaults = {
            "provider": "github",
            "repo_url": "https://github.com/user/repo",
            "issue_external_id": "100",
            "callback_url": "https://example.com/cb",
            "callback_secret": "sec",
            "status": "queued",
        }
        defaults.update(kwargs)
        return Task.objects.create(**defaults)

    def _make_payload(self, token="ghp_SECRET123"):
        return {
            "repo": {"url": "https://github.com/user/repo", "token": token},
            "issue": {"text": "Fix the thing", "external_issue_id": "100"},
            "callback": {"url": "https://example.com/cb", "secret": "sec"},
        }

    def _make_agent_result(self, **overrides):
        result = {
            "status": "done",
            "branch_name": "Jiffy/fix-thing",
            "pr_url": "https://github.com/user/repo/pull/1",
            "programming_language": "python",
            "summary": "Fixed.",
            "technical_report": None,
            "error_message": None,
            "model": None,
            "callback": {"attempted": True, "succeeded": True, "error": None},
        }
        result.update(overrides)
        return AgentResult(**{k: v for k, v in result.items() if k in AgentResult._fields})

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_log_output_is_ordered_and_readable(
        self, mock_load, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = self._make_agent_result()

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="INFO") as cm:
            execute_task(task.id)

        messages = [r.getMessage() for r in cm.records]

        for msg in messages:
            self.assertIn(f"[{task.id}|github]", msg, f"Missing task_id/provider prefix: {msg}")

        status_keywords = [
            "Task started",
            "Checking sandbox image",
            "provisioning",
            "cloning",
            "running",
            "Agent result: done",
            "Task completed",
        ]
        found = []
        for kw in status_keywords:
            for msg in messages:
                if kw in msg:
                    found.append(kw)
                    break
        self.assertEqual(found, status_keywords, f"Log order mismatch: {found}")

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_token_never_appears_in_logs(
        self, mock_load, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        secret_token = "ghp_SUPERTOKEN_12345"
        task = self._create_task()
        mock_load.return_value = self._make_payload(token=secret_token)
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = self._make_agent_result()

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="DEBUG") as cm:
            execute_task(task.id)

        for record in cm.records:
            self.assertNotIn(
                secret_token,
                record.getMessage(),
                f"Token leaked in log: {record.getMessage()}",
            )

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_failure_path_logs_error_message(
        self, mock_load, mock_ensure, mock_cb,
    ):
        task = self._create_task()
        mock_load.return_value = self._make_payload()

        with patch(
            "jobs.tasks.start_generic_sandbox_container"
        ) as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(
                side_effect=ContainerError("boom")
            )
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            from jobs.tasks import execute_task

            with self.assertLogs("jobs.tasks", level="ERROR") as cm:
                execute_task(task.id)

        messages = [r.getMessage() for r in cm.records]
        error_msgs = [m for m in messages if "boom" in m]
        self.assertTrue(error_msgs, "Error message 'boom' not found in ERROR logs")

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_provider_in_every_log_line(
        self, mock_load, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        """Every log line for a task must include the provider tag."""
        task = self._create_task(provider="gitlab")
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = self._make_agent_result()

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="INFO") as cm:
            execute_task(task.id)

        for record in cm.records:
            self.assertIn(
                "|gitlab",
                record.getMessage(),
                f"Provider tag missing in log: {record.getMessage()}",
            )

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_failed_agent_result_logs_warning(
        self, mock_load, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        """A failed agent result should log at WARNING, not INFO."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(
            status="failed",
            branch_name=None,
            pr_url=None,
            programming_language=None,
            summary=None,
            technical_report=None,
            error_message="Something went wrong",
            model=None,
            callback={"attempted": False, "succeeded": False, "error": "No callback information available"},
        )

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="WARNING") as cm:
            execute_task(task.id)

        messages = [r.getMessage() for r in cm.records]
        failed_msgs = [m for m in messages if "Agent result: failed" in m]
        self.assertTrue(failed_msgs, "No WARNING log for failed agent result")
        self.assertIn("Something went wrong", failed_msgs[0])

    @patch("jobs.tasks.send_fallback_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_callback_secret_never_in_logs(
        self, mock_load, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        """The callback secret must never appear in log output."""
        secret = "super_secret_callback_key_abc"
        task = self._create_task(callback_secret=secret)
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = self._make_agent_result()

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="DEBUG") as cm:
            execute_task(task.id)

        for record in cm.records:
            self.assertNotIn(
                secret,
                record.getMessage(),
                f"Callback secret leaked in log: {record.getMessage()}",
            )
