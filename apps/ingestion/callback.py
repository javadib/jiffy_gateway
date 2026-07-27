"""Callback dispatch — sends reports to callback_url.

Uses a declarative callback spec per provider so the agent (first attempt)
and the Gateway (fallback) build requests identically.
"""

import logging
import time
from typing import TYPE_CHECKING, Any

import requests

from jobs.callback_specs import build_callback_request, get_callback_spec

if TYPE_CHECKING:
    from jobs.models import Task

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


def send_callback(
        task: "Task",
        status: str,
        summary: str | None = None,
        pr_url: str | None = None,
        error_message: str | None = None,
        model: str | None = None,
) -> None:
    """Send a callback to task.callback_url using the provider's callback spec.

    Retries up to MAX_RETRIES times on failure. Logs failures without
    raising exceptions.

    Args:
        task: The Task instance.
        status: The final status ("done" or "failed").
        summary: Optional summary of the result.
        pr_url: Optional PR/MR URL if one was opened.
        error_message: Optional error message if the task failed.
        model: Optional LLM model used for the task.
    """
    try:
        spec = get_callback_spec(task.provider)
    except KeyError:
        logger.error(
            "Unknown provider %s for task %d — cannot build callback",
            task.provider,
            task.id,
        )
        return

    _send_callback_via_spec(
        spec=spec,
        task_id=task.id,
        callback_url=task.callback_url,
        callback_secret=task.callback_secret,
        status=status,
        summary=summary,
        branch_name=task.branch_name,
        pr_url=pr_url,
        error_message=error_message or task.error_message,
    )


def send_fallback_callback(
    task: "Task",
    status: str,
    summary: str | None = None,
    branch_name: str | None = None,
    pr_url: str | None = None,
    error_message: str | None = None,
) -> None:
    """Gateway fallback callback using the provider's spec.

    Called when the agent did not attempt or failed its own callback attempt.
    Uses the same declarative spec the agent was given.
    """
    try:
        spec = get_callback_spec(task.provider)
    except KeyError:
        logger.error(
            "Unknown provider %s for task %d — cannot send fallback callback",
            task.provider,
            task.id,
        )
        return

    _send_callback_via_spec(
        spec=spec,
        task_id=task.id,
        callback_url=task.callback_url,
        callback_secret=task.callback_secret,
        status=status,
        summary=summary,
        branch_name=branch_name,
        pr_url=pr_url,
        error_message=error_message,
    )


def _send_callback_via_spec(
    spec: dict[str, Any],
    task_id: int,
    callback_url: str,
    callback_secret: str,
    status: str,
    summary: str | None = None,
    branch_name: str | None = None,
    pr_url: str | None = None,
    error_message: str | None = None,
) -> None:
    """Low-level callback delivery using a declarative spec.

    Shared by ``send_callback`` (Gateway-owned fallback) and the agent's own
    first-attempt logic (documented in the agent instructions).
    """
    method, url, headers, body = build_callback_request(
        spec=spec,
        task_id=task_id,
        callback_url=callback_url,
        callback_secret=callback_secret,
        status=status,
        summary=summary,
        branch_name=branch_name,
        pr_url=pr_url,
        error_message=error_message,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.request(
                method, url, data=body, headers=headers, timeout=30
            )
            if response.status_code < 300:
                logger.info(
                    "Callback delivered for task %d (attempt %d)",
                    task_id,
                    attempt,
                )
                return
            logger.warning(
                "Callback for task %d returned %d (attempt %d/%d)",
                task_id,
                response.status_code,
                attempt,
                MAX_RETRIES,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Callback for task %d failed with %s (attempt %d/%d)",
                task_id,
                exc,
                attempt,
                MAX_RETRIES,
            )

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)

    logger.error(
        "All %d callback attempts failed for task %d. "
        "callback_url=%s, status=%s",
        MAX_RETRIES,
        task_id,
        callback_url,
        status,
    )
