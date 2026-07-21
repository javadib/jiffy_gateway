"""Celery tasks for job execution."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, acks_late=True, queue="execute", autoretry_for=(Exception,))
def execute_task(self, task_id: int) -> None:
    """Execute a coding task in an isolated container.

    This is a stub — the full pipeline (LLM extraction, container
    execution, commit/push, PR creation) will be implemented later.
    """
    from jobs.models import Task

    logger.info("execute_task called for task_id=%d", task_id)
    # TODO: implement full pipeline
    # For now, just mark as done
    try:
        task = Task.objects.get(id=task_id)
        task.status = "done"
        task.save(update_fields=["status", "updated_at"])
    except Task.DoesNotExist:
        logger.error("Task %d not found", task_id)
