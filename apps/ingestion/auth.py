"""Per-provider webhook authentication verification."""

import hashlib
import hmac
import os


def verify_github_signature(raw_body: bytes, signature_header: str) -> bool:
    """Verify GitHub webhook signature.

    GitHub sends the signature in the X-Hub-Signature-256 header as
    sha256=<hex_digest>, computed as HMAC-SHA256(secret, raw_body).

    Args:
        raw_body: The raw request body bytes.
        signature_header: The value of the X-Hub-Signature-256 header.

    Returns:
        True if the signature is valid, False otherwise.
    """
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_digest = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    expected_signature = f"sha256={expected_digest}"

    return hmac.compare_digest(expected_signature, signature_header)


def verify_gitlab_token(token_header: str) -> bool:
    """Verify GitLab webhook shared token.

    GitLab sends a plain shared token in the X-Gitlab-Token header.
    No HMAC — just a constant-time string comparison.

    Args:
        token_header: The value of the X-Gitlab-Token header.

    Returns:
        True if the token matches, False otherwise.
    """
    secret = os.environ.get("GITLAB_WEBHOOK_SECRET", "")
    if not secret:
        return False

    if not token_header:
        return False

    return hmac.compare_digest(secret, token_header)


def verify_gitea_signature(raw_body: bytes, signature_header: str) -> bool:
    """Verify Gitea webhook signature.

    Gitea sends the signature in the X-Gitea-Signature header as a
    hex HMAC-SHA256 digest of the raw body (no sha256= prefix).

    Args:
        raw_body: The raw request body bytes.
        signature_header: The value of the X-Gitea-Signature header.

    Returns:
        True if the signature is valid, False otherwise.
    """
    secret = os.environ.get("GITEA_WEBHOOK_SECRET", "")
    if not secret:
        return False

    if not signature_header:
        return False

    expected_digest = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_digest, signature_header)
