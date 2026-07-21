"""Serializers for ingestion endpoints."""

from rest_framework import serializers


class IngestionPayloadSerializer(serializers.Serializer):
    repo_url = serializers.URLField(max_length=500, help_text="Git repository URL")
    issue_external_id = serializers.CharField(max_length=100, help_text="External issue/thread ID from the git provider")
    thread_text = serializers.CharField(help_text="Full issue/thread text for the coding agent")
    repo_token = serializers.CharField(help_text="Short-lived repo access token for clone/push/PR operations")
    callback_url = serializers.URLField(max_length=500, help_text="URL to POST the final result to")
    callback_secret = serializers.CharField(max_length=128, help_text="HMAC secret for signing the callback request")
