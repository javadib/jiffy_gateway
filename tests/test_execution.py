"""Tests for the execution pipeline: agent instructions, result parsing, task orchestration,
sandbox image management, and logging."""

import json
import logging
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from jobs.execution.agent import AgentResult, build_agent_instructions, read_agent_result
from jobs.execution.container import (
    _extract_git_host,
    _inject_token_into_url,
    _redact_url,
    ensure_sandbox_image,
    get_docker_client,
    log_sandbox_startup,
)
from jobs.execution.exceptions import AgentError, ContainerError
from jobs.models import Task


# ---------------------------------------------------------------------------
# build_agent_instructions
# ---------------------------------------------------------------------------


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
            "error_message": None,
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "done")
        self.assertEqual(result.branch_name, "Jiffy/fix-bug")
        self.assertEqual(result.pr_url, "https://github.com/user/repo/pull/42")
        self.assertEqual(result.programming_language, "python")
        self.assertEqual(result.summary, "Fixed the bug.")
        self.assertIsNone(result.error_message)

    def test_valid_failed_result(self):
        result_data = {
            "status": "failed",
            "branch_name": "Jiffy/attempt",
            "pr_url": None,
            "programming_language": None,
            "summary": None,
            "error_message": "Could not install dependency X.",
        }
        container = self._make_container(output=json.dumps(result_data).encode())
        result = read_agent_result(container)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_message, "Could not install dependency X.")

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


# ---------------------------------------------------------------------------
# log_sandbox_startup
# ---------------------------------------------------------------------------


class LogSandboxStartupTest(TestCase):
    """Tests for log_sandbox_startup."""

    def test_runs_script_inside_container(self):
        container = MagicMock()
        # First call is from _get_opencode_model (model lookup); second is the actual script
        # Script output is truthy, so a third call writes to /proc/1/fd/1
        container.exec_run.side_effect = [
            (0, (b'{"model": "test-model"}', b"")),
            (0, (b"output", b"")),
            (0, (b"", b"")),
        ]

        log_sandbox_startup(container, task_id=42)

        self.assertEqual(container.exec_run.call_count, 3)
        _, kwargs = container.exec_run.call_args_list[1]
        cmd = kwargs["cmd"]
        self.assertIn("bash", cmd)
        self.assertIn("-l", cmd)

    def test_writes_output_to_proc_1_fd_1(self):
        container = MagicMock()
        container.exec_run.side_effect = [
            (0, (b'{"model": "test-model"}', b"")),
            (0, (b"report content here", b"")),
            (0, (b"", b"")),
        ]

        log_sandbox_startup(container, task_id=42)

        self.assertEqual(container.exec_run.call_count, 3)
        _, kwargs = container.exec_run.call_args_list[2]
        cmd_str = " ".join(kwargs["cmd"])
        self.assertIn("/proc/1/fd/1", cmd_str)

    def test_skips_write_when_output_empty(self):
        container = MagicMock()
        container.exec_run.side_effect = [
            (0, (b'{"model": "test-model"}', b"")),
            (0, (b"", b"")),
        ]

        log_sandbox_startup(container, task_id=42)

        self.assertEqual(container.exec_run.call_count, 2)

    def test_logs_warning_on_script_failure(self):
        container = MagicMock()
        # Script fails (exit 1) but still produces output → write to /proc/1/fd/1 is also called
        container.exec_run.side_effect = [
            (0, (b'{"model": "test-model"}', b"")),
            (1, (b"error happened", b"")),
            (0, (b"", b"")),
        ]

        with self.assertLogs("jobs.execution.container", level="WARNING") as cm:
            log_sandbox_startup(container, task_id=42)

        self.assertTrue(
            any("Startup report script exited with code 1" in m for m in cm.output),
        )


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
            "error_message": None,
            "model": None,
        }
        result.update(overrides)
        return result

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_happy_path(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
    ):
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(**self._make_agent_result())

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "done")
        self.assertEqual(task.branch_name, "Jiffy/fix-thing")
        self.assertEqual(task.pr_url, "https://github.com/user/repo/pull/1")
        self.assertEqual(task.programming_language, "python")
        mock_cb.assert_called_once()

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_failed_agent_result_short_circuits(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
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
            error_message="Agent failed.",
            model=None,
        )

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.error_message, "Agent failed.")
        # These should NOT have been saved from the agent result
        self.assertIsNone(task.branch_name)
        self.assertIsNone(task.pr_url)
        self.assertIsNone(task.programming_language)
        mock_cb.assert_called_once_with(
            task, status="failed", error_message="Agent failed."
        )

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_missing_payload_fails_task(self, mock_load, mock_cb):
        task = self._create_task()
        mock_load.side_effect = ValueError("Payload expired")

        from jobs.tasks import execute_task

        execute_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertIn("Payload expired", task.error_message)

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_clone_failure_fails_task(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_ensure, mock_cb
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

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_nonexistent_task_does_not_crash(self, mock_load, mock_cb):
        mock_load.return_value = self._make_payload()

        from jobs.tasks import execute_task

        # Should not raise
        execute_task(99999)

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_status_transitions(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_run, mock_result, mock_ensure, mock_cb
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
        mock_result.return_value = AgentResult(**self._make_agent_result())

        from jobs.tasks import execute_task

        with patch.object(Task, "save", tracking_save):
            execute_task(task.id)

        self.assertIn("provisioning", status_log)
        self.assertIn("cloning", status_log)
        self.assertIn("running", status_log)
        self.assertIn("reporting", status_log)
        self.assertIn("done", status_log)
        # Done should come after reporting
        self.assertGreater(status_log.index("reporting"), status_log.index("running"))
        self.assertGreater(status_log.index("done"), status_log.index("reporting"))


# ---------------------------------------------------------------------------
# Container helpers
# ---------------------------------------------------------------------------


class ExtractGitHostTest(TestCase):
    def test_https_github(self):
        self.assertEqual(_extract_git_host("https://github.com/user/repo"), "github.com")

    def test_https_gitlab(self):
        self.assertEqual(_extract_git_host("https://gitlab.com/group/project"), "gitlab.com")

    def test_ssh_url(self):
        self.assertIsNone(_extract_git_host("git@github.com:user/repo.git"))

    def test_empty_url(self):
        self.assertIsNone(_extract_git_host(""))


class InjectTokenIntoUrlTest(TestCase):
    def test_https_url(self):
        result = _inject_token_into_url("https://github.com/user/repo.git", "mytoken")
        self.assertEqual(result, "https://mytoken@github.com/user/repo.git")

    def test_https_with_port(self):
        result = _inject_token_into_url("https://gitea.example.com:8443/user/repo.git", "tok", provider="gitea", username="myuser")
        self.assertEqual(result, "https://myuser:tok@gitea.example.com:8443/user/repo.git")

    def test_ssh_url_unchanged(self):
        result = _inject_token_into_url("git@github.com:user/repo.git", "tok")
        self.assertEqual(result, "git@github.com:user/repo.git")

    def test_gitlab_with_username(self):
        result = _inject_token_into_url(
            "https://gitlab.example.com/tanuki/awesome_project.git", "glpat-xxx",
            provider="gitlab", username="tanuki",
        )
        self.assertEqual(result, "https://tanuki:glpat-xxx@gitlab.example.com/tanuki/awesome_project.git")

    def test_gitea_with_username(self):
        result = _inject_token_into_url(
            "https://gitea.domain.org/test/test.git", "gta-token",
            provider="gitea", username="testuser",
        )
        self.assertEqual(result, "https://testuser:gta-token@gitea.domain.org/test/test.git")

    def test_gitea_without_username_falls_back_to_token_only(self):
        result = _inject_token_into_url(
            "https://gitea.domain.org/test/test.git", "tok",
            provider="gitea", username="",
        )
        self.assertEqual(result, "https://tok@gitea.domain.org/test/test.git")


# ---------------------------------------------------------------------------
# URL redaction
# ---------------------------------------------------------------------------


class RedactUrlTest(TestCase):
    def test_https_with_token(self):
        url = "https://ghp_abc123@github.com/user/repo.git"
        self.assertEqual(_redact_url(url), "https://***@github.com/user/repo.git")

    def test_https_without_token(self):
        url = "https://github.com/user/repo.git"
        self.assertEqual(_redact_url(url), url)

    def test_ssh_url(self):
        url = "git@github.com:user/repo.git"
        self.assertEqual(_redact_url(url), url)


# ---------------------------------------------------------------------------
# get_docker_client
# ---------------------------------------------------------------------------


class GetDockerClientTest(TestCase):
    """Tests for the DOCKER_HOST validation in get_docker_client."""

    @patch.dict("os.environ", {"DOCKER_HOST": "tcp://docker-socket-proxy:2375"})
    @patch("jobs.execution.container.docker.from_env")
    def test_docker_host_set_returns_client(self, mock_from_env):
        """When DOCKER_HOST is set, the client is created via docker.from_env."""
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        client = get_docker_client()

        self.assertIs(client, mock_client)
        mock_from_env.assert_called_once()

    @patch.dict("os.environ", {}, clear=True)
    @patch("jobs.execution.container.docker.from_env")
    def test_no_docker_host_falls_back_to_default_socket(self, mock_from_env):
        """When DOCKER_HOST is unset, falls back to docker.from_env (local dev)."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_from_env.return_value = mock_client

        client = get_docker_client()

        self.assertIs(client, mock_client)
        mock_from_env.assert_called_once()
        mock_client.ping.assert_called_once()

    @patch.dict("os.environ", {}, clear=True)
    @patch("jobs.execution.container.docker.from_env")
    def test_no_docker_host_and_socket_unreachable_raises_error(self, mock_from_env):
        """When DOCKER_HOST is unset and default socket is unreachable, raises ContainerError."""
        mock_from_env.side_effect = ConnectionError("Cannot connect to Docker daemon")

        with self.assertRaises(ContainerError) as ctx:
            get_docker_client()

        self.assertIn("DOCKER_HOST", str(ctx.exception))

    @patch.dict("os.environ", {}, clear=True)
    @patch("jobs.execution.container.docker.from_env")
    def test_no_docker_host_ping_fails_raises_error(self, mock_from_env):
        """When DOCKER_HOST is unset and ping fails, raises ContainerError."""
        mock_client = MagicMock()
        mock_client.ping.side_effect = ConnectionError("connection refused")
        mock_from_env.return_value = mock_client

        with self.assertRaises(ContainerError) as ctx:
            get_docker_client()

        self.assertIn("DOCKER_HOST", str(ctx.exception))

    @patch.dict("os.environ", {"DOCKER_HOST": "tcp://docker-socket-proxy:2375"})
    @patch("jobs.execution.container.docker.from_env")
    def test_docker_host_set_skips_ping(self, mock_from_env):
        """When DOCKER_HOST is set, no connectivity check is performed."""
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        get_docker_client()

        mock_client.ping.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_sandbox_image
# ---------------------------------------------------------------------------


@override_settings(SANDBOX_IMAGE="jiffy-sandbox:1.1.0")
class EnsureSandboxImageTest(TestCase):
    """Tests for the sandbox image auto-build logic."""

    @patch("jobs.execution.container.get_docker_client")
    def test_existing_image_skips_build(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        # images.get succeeds → image exists
        mock_client.images.get.return_value = MagicMock()

        ensure_sandbox_image()

        mock_client.images.get.assert_called_once_with("jiffy-sandbox:1.1.0")
        mock_client.images.build.assert_not_called()

    @patch("jobs.execution.container.get_docker_client")
    def test_missing_image_triggers_build(self, mock_get_client):
        from docker.errors import ImageNotFound as _IN

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        # images.get raises → image missing
        mock_client.images.get.side_effect = _IN("not found")
        mock_client.images.build.return_value = (MagicMock(), [])

        ensure_sandbox_image()

        mock_client.images.get.assert_called_once()
        mock_client.images.build.assert_called_once()
        build_kwargs = mock_client.images.build.call_args
        self.assertEqual(build_kwargs[1]["tag"], "jiffy-sandbox:1.1.0")

    @patch("jobs.execution.container.get_docker_client")
    def test_build_failure_raises_container_error(self, mock_get_client):
        from docker.errors import ImageNotFound as _IN

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.images.get.side_effect = _IN("not found")
        mock_client.images.build.side_effect = RuntimeError("Docker daemon down")

        with self.assertRaises(ContainerError):
            ensure_sandbox_image()

    @patch("jobs.execution.container.get_docker_client")
    def test_existing_image_log_includes_timing(self, mock_get_client):
        """The 'found locally' log message should include elapsed time."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()

        with self.assertLogs("jobs.execution.container", level="INFO") as cm:
            ensure_sandbox_image()

        messages = [r.getMessage() for r in cm.records]
        self.assertTrue(
            any("no build needed" in m and "0." in m for m in messages),
            f"Expected timing in log, got: {messages}",
        )

    @patch("jobs.execution.container.get_docker_client")
    def test_build_log_includes_timing(self, mock_get_client):
        """The 'built successfully' log message should include elapsed time."""
        from docker.errors import ImageNotFound as _IN

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.images.get.side_effect = _IN("not found")
        mock_client.images.build.return_value = (MagicMock(), [])

        with self.assertLogs("jobs.execution.container", level="INFO") as cm:
            ensure_sandbox_image()

        messages = [r.getMessage() for r in cm.records]
        self.assertTrue(
            any("built successfully" in m and "in " in m for m in messages),
            f"Expected build timing in log, got: {messages}",
        )


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

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_log_output_is_ordered_and_readable(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(**{
            "status": "done",
            "branch_name": "Jiffy/fix-thing",
            "pr_url": "https://github.com/user/repo/pull/1",
            "programming_language": "python",
            "summary": "Fixed.",
            "error_message": None,
            "model": None,
        })

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="INFO") as cm:
            execute_task(task.id)

        messages = [r.getMessage() for r in cm.records]

        # Every line for this task should have the task_id prefix with provider
        for msg in messages:
            self.assertIn(f"[{task.id}|github]", msg, f"Missing task_id/provider prefix: {msg}")

        # Verify the ordered sequence of status transitions
        status_keywords = [
            "Task started",
            "Checking sandbox image",
            "provisioning",
            "cloning",
            "running",
            "Agent result: done",
            "reporting",
            "Task completed",
        ]
        found = []
        for kw in status_keywords:
            for msg in messages:
                if kw in msg:
                    found.append(kw)
                    break
        self.assertEqual(found, status_keywords, f"Log order mismatch: {found}")

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_token_never_appears_in_logs(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        secret_token = "ghp_SUPERTOKEN_12345"
        task = self._create_task()
        mock_load.return_value = self._make_payload(token=secret_token)
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(**{
            "status": "done",
            "branch_name": "Jiffy/fix",
            "pr_url": None,
            "programming_language": None,
            "summary": "Done.",
            "error_message": None,
            "model": None,
        })

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="DEBUG") as cm:
            execute_task(task.id)

        for record in cm.records:
            self.assertNotIn(
                secret_token,
                record.getMessage(),
                f"Token leaked in log: {record.getMessage()}",
            )

    @patch("jobs.tasks.send_callback")
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

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_provider_in_every_log_line(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        """Every log line for a task must include the provider tag."""
        task = self._create_task(provider="gitlab")
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(**{
            "status": "done",
            "branch_name": "Jiffy/fix",
            "pr_url": None,
            "programming_language": None,
            "summary": "Done.",
            "error_message": None,
            "model": None,
        })

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="INFO") as cm:
            execute_task(task.id)

        for record in cm.records:
            self.assertIn(
                "|gitlab",
                record.getMessage(),
                f"Provider tag missing in log: {record.getMessage()}",
            )

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_failed_agent_result_logs_warning(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        """A failed agent result should log at WARNING, not INFO."""
        task = self._create_task()
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(**{
            "status": "failed",
            "branch_name": None,
            "pr_url": None,
            "programming_language": None,
            "summary": None,
            "error_message": "Something went wrong",
            "model": None,
        })

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="WARNING") as cm:
            execute_task(task.id)

        messages = [r.getMessage() for r in cm.records]
        failed_msgs = [m for m in messages if "Agent result: failed" in m]
        self.assertTrue(failed_msgs, "No WARNING log for failed agent result")
        self.assertIn("Something went wrong", failed_msgs[0])

    @patch("jobs.tasks.send_callback")
    @patch("jobs.tasks.ensure_sandbox_image")
    @patch("jobs.tasks.read_agent_result")
    @patch("jobs.tasks.run_agent_in_container")
    @patch("jobs.tasks.clone_repo_in_container")
    @patch("jobs.tasks.start_generic_sandbox_container")
    @patch("jobs.tasks.log_sandbox_startup")
    @patch("jobs.tasks.load_payload_from_redis")
    def test_callback_secret_never_in_logs(
        self, mock_load, mock_log_startup, mock_container, mock_clone, mock_run,
        mock_result, mock_ensure, mock_cb,
    ):
        """The callback secret must never appear in log output."""
        secret = "super_secret_callback_key_abc"
        task = self._create_task(callback_secret=secret)
        mock_load.return_value = self._make_payload()
        mock_container.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_container.return_value.__exit__ = MagicMock(return_value=False)
        mock_result.return_value = AgentResult(**{
            "status": "done",
            "branch_name": "Jiffy/fix",
            "pr_url": None,
            "programming_language": None,
            "summary": "Done.",
            "error_message": None,
            "model": None,
        })

        from jobs.tasks import execute_task

        with self.assertLogs("jobs.tasks", level="DEBUG") as cm:
            execute_task(task.id)

        for record in cm.records:
            self.assertNotIn(
                secret,
                record.getMessage(),
                f"Callback secret leaked in log: {record.getMessage()}",
            )
