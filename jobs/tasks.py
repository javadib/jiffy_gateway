"""Celery tasks for job execution."""

import logging
import time
from typing import Any

from celery import shared_task

from apps.ingestion.callback import send_fallback_callback
from jobs.execution.agent import (
    AgentResult,
    build_agent_instructions,
    read_agent_result,
)
from jobs.execution.container import (
    clone_repo_in_container,
    ensure_sandbox_image,
    run_agent_in_container,
    start_generic_sandbox_container,
)
from jobs.execution.exceptions import ExecutionError
from jobs.models import Task
from jobs.utils.redis import load_payload_from_redis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_log(
    task_id: int,
    level: int,
    msg: str,
    *args: Any,
    provider: str = "",
    **kwargs: Any,
) -> None:
    """Log a message prefixed with ``[task_id]`` (and optionally provider) for easy grep."""
    prefix = f"[{task_id}]"
    if provider:
        prefix = f"[{task_id}|{provider}]"
    logger.log(level, f"{prefix} {msg}", *args, **kwargs)


def _update_status(task: Task, status: str) -> None:
    """Update the task status in its own short write transaction."""
    task.status = status
    task.save(update_fields=["status", "updated_at"])


def _agent_callback_succeeded(result: AgentResult | None) -> bool:
    """Return True if the agent successfully delivered its own callback."""
    if result is None:
        return False
    cb = result.callback
    if not isinstance(cb, dict):
        return False
    return bool(cb.get("attempted")) and bool(cb.get("succeeded"))


def _handle_callback(
    task: Task,
    result: AgentResult | None,
    status: str,
    summary: str | None = None,
    branch_name: str | None = None,
    pr_url: str | None = None,
    error_message: str | None = None,
) -> None:
    """Handle callback delivery: agent-first, Gateway fallback.

    If the agent already delivered the callback successfully, skip Gateway
    callback.  Otherwise fall back to the Gateway sending via the spec.
    """
    if _agent_callback_succeeded(result):
        _task_log(
            task.id,
            logging.INFO,
            "Agent already delivered callback successfully — skipping Gateway callback",
            provider=task.provider,
        )
        return

    _task_log(
        task.id,
        logging.WARNING,
        "Agent callback not delivered (attempted=%s, succeeded=%s) — Gateway falling back",
        result.callback.get("attempted", False) if result else "N/A",
        result.callback.get("succeeded", False) if result else "N/A",
        provider=task.provider,
    )

    send_fallback_callback(
        task,
        status=status,
        summary=summary,
        branch_name=branch_name,
        pr_url=pr_url,
        error_message=error_message,
    )


def _fail_task(task: Task, error_message: str, result: AgentResult | None = None) -> None:
    """Mark a task as failed and handle callback (agent-first, Gateway fallback)."""
    task.status = "failed"
    task.error_message = error_message
    task.save(update_fields=["status", "error_message", "updated_at"])
    _task_log(task.id, logging.ERROR, "Task failed: %s", error_message, provider=task.provider)
    _handle_callback(task, result, status="failed", error_message=error_message)


def _redact_payload_for_log(payload: dict) -> dict:
    """Return a shallow copy of the payload with secrets masked for logging."""
    redacted = dict(payload)
    repo = dict(redacted.get("repo", {}))
    repo["token"] = "***"
    redacted["repo"] = repo
    callback = dict(redacted.get("callback", {}))
    callback["secret"] = "***"
    redacted["callback"] = callback
    return redacted


# ---------------------------------------------------------------------------
# Main task
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    queue="execute",
)
def execute_task(self, task_id: int) -> None:
    """Execute a coding task in an isolated sandbox container.

    Sequence:
      0. Load task + payload, log start
      1. Ensure sandbox image exists (build if missing)
      2. Provision the generic sandbox container
      3. Clone the repo into it
      4. Hand off to the agent with instructions
      5. Read the agent's structured result
      6. Report via callback
    """
    start_time = time.monotonic()

    # --- Load task and payload ------------------------------------------------
    try:
        task = Task.objects.get(id=task_id)
    except Task.DoesNotExist:
        logger.error("[task_id=%d] Task not found in DB — skipping", task_id)
        return

    _task_log(task_id, logging.INFO, "Task started", provider=task.provider)

    try:
        payload = load_payload_from_redis(task_id)
    except ValueError as e:
        _task_log(task_id, logging.ERROR, "Failed to load payload: %s", e, provider=task.provider)
        _fail_task(task, str(e))
        return

    repo_url = payload["repo"]["url"]
    repo_token = payload["repo"]["token"]
    repo_username = payload["repo"].get("username", "")
    callback = payload["callback"]
    issue_id = payload.get("issue", {}).get("external_issue_id", "?")
    _task_log(
        task_id,
        logging.INFO,
        "Payload loaded — repo=%s issue=%s",
        repo_url,
        issue_id,
        provider=task.provider,
    )

    # --- Ensure sandbox image exists -----------------------------------------
    try:
        _task_log(task_id, logging.INFO, "Checking sandbox image...", provider=task.provider)
        ensure_sandbox_image()
    except ExecutionError as e:
        _task_log(task_id, logging.ERROR, "Sandbox image check/build failed: %s", e, provider=task.provider)
        _fail_task(task, str(e))
        return

    # --- Provision → Clone → Run → Report ------------------------------------
    result: AgentResult | None = None
    try:
        _update_status(task, "provisioning")
        _task_log(task_id, logging.INFO, "Status → provisioning", provider=task.provider)

        env_vars = {"REPO_TOKEN": repo_token}

        with start_generic_sandbox_container(task.id, env_vars, repo_url=repo_url) as container:
            # Cloning
            _update_status(task, "cloning")
            _task_log(task_id, logging.INFO, "Status → cloning", provider=task.provider)
            clone_repo_in_container(
                container, repo_url, repo_token, task_id=task_id,
                provider=task.provider, username=repo_username,
            )

            # Running — agent does everything from here
            _update_status(task, "running")
            _task_log(task_id, logging.INFO, "Status → running — handing off to agent", provider=task.provider)
            instructions = build_agent_instructions(payload)
            run_agent_in_container(container, instructions, task_id=task_id)

            # Read result
            result = read_agent_result(container)

        if result.status == "done":
            _task_log(
                task_id,
                logging.INFO,
                "Agent result: done — model=%s branch=%s pr=%s lang=%s callback=%s",
                result.model or "(unknown)",
                result.branch_name or "(none)",
                result.pr_url or "(none)",
                result.programming_language or "(none)",
                result.callback or "(none)",
                provider=task.provider,
            )
        else:
            _task_log(
                task_id,
                logging.WARNING,
                "Agent result: failed — model=%s error=%s callback=%s",
                result.model or "(unknown)",
                result.error_message or "(no details)",
                result.callback or "(none)",
                provider=task.provider,
            )

        if result.status != "done":
            _fail_task(task, error_message=result.error_message or "Agent reported failure without details.", result=result)
            return

        task.branch_name = result.branch_name
        task.programming_language = result.programming_language
        task.pr_url = result.pr_url
        task.save(update_fields=["branch_name", "programming_language", "pr_url"])

        # Callback: agent attempted first, Gateway falls back if needed
        _handle_callback(
            task,
            result=result,
            status="done",
            summary=result.summary,
            branch_name=result.branch_name,
            pr_url=result.pr_url,
        )
        _update_status(task, "done")

        elapsed = time.monotonic() - start_time
        _task_log(
            task_id,
            logging.INFO,
            "Task completed successfully in %.1fs",
            elapsed,
            provider=task.provider,
        )

    except ExecutionError as e:
        elapsed = time.monotonic() - start_time
        _task_log(
            task_id,
            logging.ERROR,
            "Execution failed after %.1fs: %s",
            elapsed,
            e,
            provider=task.provider,
        )
        _fail_task(task, str(e), result=result)
    except Exception as e:
        elapsed = time.monotonic() - start_time
        _task_log(
            task_id,
            logging.ERROR,
            "Unexpected error after %.1fs: %s",
            elapsed,
            e,
            provider=task.provider,
        )
        _fail_task(task, "An unexpected internal error occurred.", result=result)
        raise self.retry(exc=e)
