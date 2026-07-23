"""Align Task schema with the AGENTS.md spec.

Renames external_issue_id → issue_external_id, removes leftover fields from
the old per-language-image design, and updates the status choices.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0002_increase_callback_url_max_length"),
    ]

    operations = [
        # Rename to match current model
        migrations.RenameField(
            model_name="task",
            old_name="external_issue_id",
            new_name="issue_external_id",
        ),
        # Remove leftover fields from the old per-language design
        migrations.RemoveField(model_name="task", name="title"),
        migrations.RemoveField(model_name="task", name="branch_base"),
        migrations.RemoveField(model_name="task", name="pr_request"),
        migrations.RemoveField(model_name="task", name="code_review_request"),
        # Update status choices to match AGENTS.md
        migrations.AlterField(
            model_name="task",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("provisioning", "Provisioning sandbox"),
                    ("cloning", "Cloning"),
                    ("running", "Running"),
                    ("reporting", "Reporting"),
                    ("done", "Done"),
                    ("failed", "Failed"),
                ],
                default="queued",
                max_length=20,
            ),
        ),
    ]
