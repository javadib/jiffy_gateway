"""Serializers for ingestion endpoints."""

from rest_framework import serializers


class RepoSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=500, help_text="Git repository URL")
    token = serializers.CharField(help_text="Short-lived repo access token for clone/push/PR operations")


class IssueSerializer(serializers.Serializer):
    text = serializers.CharField(help_text="Full issue/thread text for the coding agent")
    issue_external_id = serializers.CharField(max_length=100, help_text="External issue/thread ID from the git provider")


class CallbackSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=500, help_text="URL to POST the final result to")
    secret = serializers.CharField(max_length=128, help_text="HMAC secret for signing the callback request")


class IngestionPayloadSerializer(serializers.Serializer):
    repo = RepoSerializer()
    issue = IssueSerializer()
    callback = CallbackSerializer()
