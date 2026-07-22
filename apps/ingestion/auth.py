"""Ingestion authentication — header-based token verification."""

import hmac
import os

AUTH_HEADER = "X_JIFFY_TOKEN"


def verify_ingest_token(request, expected_secret: str) -> bool:
    """Verify the ``X_JIFFY_TOKEN`` header against an expected secret.

    A single, uniform header for all providers — Jiffy's own edge
    components call Jiffy's own endpoints, so there is no need to
    preserve each provider's native webhook-signing convention.

    Args:
        request: The Django request object.
        expected_secret: The provider-specific secret to compare against.

    Returns:
        True if the token matches, False otherwise.
    """
    if not expected_secret:
        return False

    token = request.META.get(AUTH_HEADER, "")
    if not token:
        return False

    return hmac.compare_digest(expected_secret, token)


def get_ingest_secret(provider: str) -> str:
    """Return the configured ingest token for *provider*.

    Each provider has its own independently configured secret via its own
    env var — they are never shared across providers.

    Args:
        provider: One of ``"github"``, ``"gitlab"``, ``"gitea"``.

    Returns:
        The secret string, or empty string if not configured.
    """
    env_map = {
        "github": "GITHUB_INGEST_TOKEN",
        "gitlab": "GITLAB_INGEST_TOKEN",
        "gitea": "GITEA_INGEST_TOKEN",
    }
    return os.environ.get(env_map.get(provider, ""), "")
