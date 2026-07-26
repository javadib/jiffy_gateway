"""Serializers for ingestion endpoints."""

from rest_framework import serializers


class RepoSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=500, help_text="Git repository URL")
    token = serializers.CharField(help_text="Short-lived repo access token for clone/push/PR operations")
    username = serializers.CharField(
        max_length=100,
        required=False,
        default="",
        help_text="Git provider username (required for GitLab/Gitea, ignored for GitHub)",
    )


class IssueSerializer(serializers.Serializer):
    text = serializers.CharField(help_text="Full issue/thread text for the coding agent")
    external_issue_id = serializers.CharField(max_length=100, help_text="External issue/thread ID from the git provider")


class CallbackSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=500, help_text="URL to POST the final result to")
    secret = serializers.CharField(max_length=1024, help_text="Opaque secret passed through to the callback endpoint")


class IngestionPayloadSerializer(serializers.Serializer):
    repo = RepoSerializer()
    issue = IssueSerializer()
    callback = CallbackSerializer()
