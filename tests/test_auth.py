"""Tests for ingestion token verification."""

import os
from unittest.mock import MagicMock, patch

from apps.ingestion.auth import get_ingest_secret, verify_ingest_token


META_KEY = "X-JIFFY-TOKEN"


def _make_request(token: str = None) -> MagicMock:
    request = MagicMock()
    if token is not None:
        request.headers = {META_KEY: token}
    else:
        request.headers = {}
    return request


class TestVerifyIngestToken:
    """Tests for the shared verify_ingest_token function."""

    def test_valid_token(self):
        request = _make_request("secret123")
        assert verify_ingest_token(request, "secret123") is True

    def test_invalid_token(self):
        request = _make_request("wrong")
        assert verify_ingest_token(request, "secret123") is False

    def test_missing_header(self):
        request = _make_request()
        assert verify_ingest_token(request, "secret123") is False

    def test_empty_header(self):
        request = _make_request("")
        assert verify_ingest_token(request, "secret123") is False

    def test_no_secret_configured(self):
        request = _make_request("anything")
        assert verify_ingest_token(request, "") is False

    def test_tampered_token(self):
        request = _make_request("secret123!")
        assert verify_ingest_token(request, "secret123") is False

    def test_other_header_not_accepted(self):
        request = MagicMock()
        request.headers = {"X-Other-Token": "secret123"}
        assert verify_ingest_token(request, "secret123") is False


class TestGetIngestSecret:
    """Tests for get_ingest_secret env-var lookup."""

    @patch.dict(os.environ, {"GITHUB_INGEST_TOKEN": "gh-tok"})
    def test_github(self):
        assert get_ingest_secret("github") == "gh-tok"

    @patch.dict(os.environ, {"GITLAB_INGEST_TOKEN": "gl-tok"})
    def test_gitlab(self):
        assert get_ingest_secret("gitlab") == "gl-tok"

    @patch.dict(os.environ, {"GITEA_INGEST_TOKEN": "gt-tok"})
    def test_gitea(self):
        assert get_ingest_secret("gitea") == "gt-tok"

    @patch.dict(os.environ, {"GITHUB_INGEST_TOKEN": ""})
    def test_github_not_configured(self):
        assert get_ingest_secret("github") == ""
