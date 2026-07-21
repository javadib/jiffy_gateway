"""Serializers for ingestion endpoints."""

from rest_framework import serializers


class IngestionPayloadSerializer(serializers.Serializer):
    repo_url = serializers.URLField()
    issue_external_id = serializers.CharField()
    thread_text = serializers.CharField()
    repo_token = serializers.CharField()
    callback_url = serializers.URLField()
    callback_secret = serializers.CharField()
