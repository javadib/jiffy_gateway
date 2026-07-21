from django.db import models


class Task(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("extracting", "Extracting requirements"),
        ("planning", "Planning"),
        ("cloning", "Cloning"),
        ("running", "Running"),
        ("verifying", "Verifying changes"),
        ("committing", "Committing"),
        ("pushing", "Pushing"),
        ("opening_pr", "Opening PR"),
        ("reporting", "Reporting"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    provider = models.CharField(max_length=20)  # "github" | "gitlab" | "gitea"
    repo_url = models.CharField(max_length=500)
    programming_language = models.CharField(
        max_length=50, null=True, blank=True
    )  # LLM-extracted
    issue_external_id = models.CharField(max_length=100)
    title = models.CharField(max_length=255, null=True, blank=True)  # LLM-extracted
    branch_base = models.CharField(
        max_length=255, null=True, blank=True
    )  # LLM-extracted
    branch_name = models.CharField(
        max_length=255, null=True, blank=True
    )  # LLM-extracted
    pr_request = models.BooleanField(default=False)  # LLM-extracted
    code_review_request = models.BooleanField(default=False)  # LLM-extracted
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="queued"
    )
    callback_url = models.URLField(max_length=500)
    callback_secret = models.CharField(max_length=128)
    pr_url = models.URLField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Task #{self.id} ({self.provider}) - {self.status}"
