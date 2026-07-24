from django.db import models


class Task(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("provisioning", "Provisioning sandbox"),
        ("cloning", "Cloning"),
        ("running", "Running"),
        ("reporting", "Reporting"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    provider = models.CharField(max_length=20)  # "github" | "gitlab" | "gitea"
    repo_url = models.CharField(max_length=500)
    issue_external_id = models.CharField(max_length=100)
    programming_language = models.CharField(max_length=50, null=True, blank=True)  # populated from the agent's final
                                                                                    # result, for audit/reporting only
    branch_name = models.CharField(max_length=255, null=True, blank=True)  # populated from the agent's final result
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    callback_url = models.URLField()
    callback_secret = models.CharField(max_length=128)
    pr_url = models.URLField(null=True, blank=True)  # populated from the agent's final result, if it opened one
    error_message = models.TextField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Task #{self.id} ({self.provider}) - {self.status}"
