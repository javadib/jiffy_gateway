"""Tests for per-provider webhook authentication."""

import hashlib
import hmac
import os
from unittest.mock import patch

from apps.ingestion.auth import verify_gitea_signature, verify_github_signature, verify_gitlab_token


class TestGitHubAuth:
    """Tests for GitHub webhook signature verification."""

    def test_valid_signature(self):
        secret = "test-github-secret"
        body = b'{"action": "opened"}'
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        signature = f"sha256={expected}"

        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": secret}):
            assert verify_github_signature(body, signature) is True

    def test_invalid_signature(self):
        secret = "test-github-secret"
        body = b'{"action": "opened"}'

        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": secret}):
            assert verify_github_signature(body, "sha256=invalid") is False

    def test_tampered_payload(self):
        secret = "test-github-secret"
        original_body = b'{"action": "opened"}'
        tampered_body = b'{"action": "closed"}'
        expected = hmac.new(secret.encode(), original_body, hashlib.sha256).hexdigest()
        signature = f"sha256={expected}"

        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": secret}):
            assert verify_github_signature(tampered_body, signature) is False

    def test_missing_prefix(self):
        secret = "test-github-secret"
        body = b'{"action": "opened"}'
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": secret}):
            assert verify_github_signature(body, digest) is False

    def test_empty_signature(self):
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "secret"}):
            assert verify_github_signature(b"body", "") is False

    def test_no_secret_configured(self):
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": ""}):
            assert verify_github_signature(b"body", "sha256=abc") is False


class TestGitLabAuth:
    """Tests for GitLab shared token verification."""

    def test_valid_token(self):
        with patch.dict(os.environ, {"GITLAB_WEBHOOK_SECRET": "my-token"}):
            assert verify_gitlab_token("my-token") is True

    def test_invalid_token(self):
        with patch.dict(os.environ, {"GITLAB_WEBHOOK_SECRET": "my-token"}):
            assert verify_gitlab_token("wrong-token") is False

    def test_tampered_token(self):
        with patch.dict(os.environ, {"GITLAB_WEBHOOK_SECRET": "my-token"}):
            assert verify_gitlab_token("my-token!") is False

    def test_empty_token(self):
        with patch.dict(os.environ, {"GITLAB_WEBHOOK_SECRET": "my-token"}):
            assert verify_gitlab_token("") is False

    def test_no_secret_configured(self):
        with patch.dict(os.environ, {"GITLAB_WEBHOOK_SECRET": ""}):
            assert verify_gitlab_token("any-token") is False


class TestGiteaAuth:
    """Tests for Gitea webhook signature verification."""

    def test_valid_signature(self):
        secret = "test-gitea-secret"
        body = b'{"action": "opened"}'
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.dict(os.environ, {"GITEA_WEBHOOK_SECRET": secret}):
            assert verify_gitea_signature(body, expected) is True

    def test_invalid_signature(self):
        secret = "test-gitea-secret"
        body = b'{"action": "opened"}'

        with patch.dict(os.environ, {"GITEA_WEBHOOK_SECRET": secret}):
            assert verify_gitea_signature(body, "invalid") is False

    def test_tampered_payload(self):
        secret = "test-gitea-secret"
        original_body = b'{"action": "opened"}'
        tampered_body = b'{"action": "closed"}'
        expected = hmac.new(secret.encode(), original_body, hashlib.sha256).hexdigest()

        with patch.dict(os.environ, {"GITEA_WEBHOOK_SECRET": secret}):
            assert verify_gitea_signature(tampered_body, expected) is False

    def test_gitea_no_prefix(self):
        """Gitea sends raw hex, not sha256= prefix."""
        secret = "test-gitea-secret"
        body = b'{"action": "opened"}'
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.dict(os.environ, {"GITEA_WEBHOOK_SECRET": secret}):
            assert verify_gitea_signature(body, expected) is True
            assert verify_gitea_signature(body, f"sha256={expected}") is False

    def test_empty_signature(self):
        with patch.dict(os.environ, {"GITEA_WEBHOOK_SECRET": "secret"}):
            assert verify_gitea_signature(b"body", "") is False

    def test_no_secret_configured(self):
        with patch.dict(os.environ, {"GITEA_WEBHOOK_SECRET": ""}):
            assert verify_gitea_signature(b"body", "abc") is False
