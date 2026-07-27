"""Ingestion authentication — header-based token verification."""

import hmac
import os

AUTH_HEADER = "X-JIFFY-TOKEN"


def verify_ingest_token(request, expected_secret: str) -> bool:
    if not expected_secret:
        return False

    token = request.headers.get(AUTH_HEADER, "")
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
