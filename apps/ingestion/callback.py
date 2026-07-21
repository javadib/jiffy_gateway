"""Callback dispatch — sends signed reports to callback_url."""

import hashlib
import hmac
import json
import logging
import time
from typing import TYPE_CHECKING

import requests

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
) -> None:
    """Send a signed callback to task.callback_url.

    Retries up to MAX_RETRIES times on failure. Logs failures without
    raising exceptions.

    Args:
        task: The Task instance.
        status: The final status ("done" or "failed").
        summary: Optional summary of the result.
        pr_url: Optional PR/MR URL if one was opened.
        error_message: Optional error message if the task failed.
    """
    payload: dict = {"task_id": task.id, "status": status}
    if summary is not None:
        payload["summary"] = summary
    if pr_url is not None:
        payload["pr_url"] = pr_url
    if error_message is not None:
        payload["error_message"] = error_message

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    signature = hmac.new(
        task.callback_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Jiffy-Signature": signature,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                task.callback_url, data=body, headers=headers, timeout=30
            )
            if response.status_code < 300:
                logger.info(
                    "Callback delivered for task %d (attempt %d)",
                    task.id,
                    attempt,
                )
                return
            logger.warning(
                "Callback for task %d returned %d (attempt %d/%d)",
                task.id,
                response.status_code,
                attempt,
                MAX_RETRIES,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Callback for task %d failed with %s (attempt %d/%d)",
                task.id,
                exc,
                attempt,
                MAX_RETRIES,
            )

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)

    logger.error(
        "All %d callback attempts failed for task %d. "
        "callback_url=%s, status=%s, payload=%s",
        MAX_RETRIES,
        task.id,
        task.callback_url,
        status,
        payload,
    )
