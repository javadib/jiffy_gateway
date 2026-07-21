"""Ingestion authentication — query-string token verification."""

import hmac
import os


def verify_ingest_token(request, expected_secret: str) -> bool:
    """Verify the ``token`` query parameter against an expected secret.

    Some edge-component environments make it hard to set a custom HTTP
    header on outgoing requests, so the shared token travels as a query
    parameter on the ingestion URL instead.  This callback-facing endpoint
    is not implementing each provider's native webhook-signing convention
    — it's Jiffy's own edge component calling Jiffy's own endpoint — so
    there's no need to preserve GitHub/Gitea-specific HMAC-over-body
    signing here.  A single, uniform check is simpler and is preferred
    given this project's "avoid unnecessary complexity" principle.

    Args:
        request: The Django request object (reads ``request.query_params``).
        expected_secret: The provider-specific secret to compare against.

    Returns:
        True if the token matches, False otherwise.
    """
    if not expected_secret:
        return False

    token = request.query_params.get("token", "")
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
