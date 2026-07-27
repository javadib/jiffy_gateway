"""Declarative callback specs per provider.

Each spec describes how to call that provider's callback endpoint:
HTTP method, auth header name/value prefix, content type, and which fields
go in the body, query string, or headers.

Adding a new provider means adding a new entry here — no pipeline code changes.
"""

from typing import Any

PROVIDER_NAMES = ("github", "gitlab", "gitea")

CALLBACK_SPECS: dict[str, dict[str, Any]] = {
    "github": {
        "method": "POST",
        "auth_header": "Authorization",
        "auth_value_prefix": "Bearer ",
        "content_type": "application/json",
        "body_fields": [
            "task_id",
            "status",
            "summary",
            "branch_name",
            "pr_url",
            "error_message",
        ],
        "query_fields": [],
        "header_fields": [],
    },
    "gitlab": {
        "method": "POST",
        "auth_header": "Authorization",
        "auth_value_prefix": "Bearer ",
        "content_type": "application/json",
        "body_fields": [
            "task_id",
            "status",
            "summary",
            "branch_name",
            "pr_url",
            "error_message",
        ],
        "query_fields": [],
        "header_fields": [],
    },
    "gitea": {
        "method": "POST",
        "auth_header": "Authorization",
        "auth_value_prefix": "Bearer ",
        "content_type": "application/json",
        "body_fields": [
            "task_id",
            "status",
            "summary",
            "branch_name",
            "pr_url",
            "error_message",
        ],
        "query_fields": [],
        "header_fields": [],
    },
}


def get_callback_spec(provider: str) -> dict[str, Any]:
    """Return the callback spec for *provider*, raising KeyError if unknown."""
    if provider not in CALLBACK_SPECS:
        raise KeyError(
            f"Unknown provider {provider!r}. Known providers: {', '.join(CALLBACK_SPECS)}"
        )
    return CALLBACK_SPECS[provider]


def build_callback_payload(
    spec: dict[str, Any],
    task_id: int,
    status: str,
    summary: str | None = None,
    branch_name: str | None = None,
    pr_url: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Build the body payload as defined by *spec*.

    Only includes fields listed in the spec's ``body_fields`` that are
    non-``None``, plus ``task_id`` and ``status`` (both always included).
    """
    payload: dict[str, Any] = {"task_id": task_id, "status": status}
    for field in spec.get("body_fields", []):
        if field in ("task_id", "status"):
            continue
        val = {
            "summary": summary,
            "branch_name": branch_name,
            "pr_url": pr_url,
            "error_message": error_message,
        }.get(field)
        if val is not None:
            payload[field] = val
    return payload


def build_callback_request(
    spec: dict[str, Any],
    task_id: int,
    callback_url: str,
    callback_secret: str,
    status: str,
    summary: str | None = None,
    branch_name: str | None = None,
    pr_url: str | None = None,
    error_message: str | None = None,
) -> tuple[str, str, dict[str, str], bytes]:
    """Build the (method, url, headers, body) for a callback request.

    Uses the declarative *spec* so the caller doesn't have to know provider-
    specific details.
    """
    method = spec["method"]
    body = build_callback_payload(
        spec,
        task_id=task_id,
        status=status,
        summary=summary,
        branch_name=branch_name,
        pr_url=pr_url,
        error_message=error_message,
    )

    import json

    body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")

    headers: dict[str, str] = {
        "Content-Type": spec["content_type"],
        spec["auth_header"]: spec["auth_value_prefix"] + callback_secret,
    }

    for field_name in spec.get("header_fields", []):
        val = {
            "task_id": str(task_id),
            "status": status,
            "summary": summary,
            "branch_name": branch_name,
            "pr_url": pr_url,
            "error_message": error_message,
        }.get(field_name)
        if val is not None:
            headers[field_name] = str(val)

    return method, callback_url, headers, body_bytes
