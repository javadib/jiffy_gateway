"""Celery tasks for ingestion (re-export from jobs.tasks)."""

from jobs.tasks import execute_task

__all__ = ["execute_task"]
