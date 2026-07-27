"""Handles agent instructions and result parsing."""
import json
import logging
from typing import Any, Dict, NamedTuple

from docker.models.containers import Container

from jobs.callback_specs import get_callback_spec

logger = logging.getLogger(__name__)

AGENT_RESULT_PATH = "/workspace/.jiffy_result.json"


class AgentResult(NamedTuple):
    status: str
    branch_name: str | None
    pr_url: str | None
    programming_language: str | None
    summary: str | None
    error_message: str | None
    model: str | None
    callback: dict | None


def build_agent_instructions(payload: Dict[str, Any]) -> str:
    """Build the instructions text handed to the coding agent.

    The instructions are agent-agnostic — they describe the contract without
    assuming any particular CLI conventions.
    """
    issue_text = payload.get("issue", {}).get("text", "")
    provider = payload.get("repo", {}).get("provider_hint", "github")
    callback_url = payload.get("callback", {}).get("url", "")
    callback_secret = payload.get("callback", {}).get("secret", "")

    # Resolve provider from repo URL if not explicitly set
    if provider == "github" and "gitlab" in payload.get("repo", {}).get("url", ""):
        provider = "gitlab"
    elif provider == "github" and "gitea" in payload.get("repo", {}).get("url", ""):
        provider = "gitea"

    spec = get_callback_spec(provider)
    spec_json = json.dumps(spec, indent=2)

    return f"""\
You are a coding agent working in an isolated sandbox environment.

## Issue / Request

The following is the full, verbatim text of the task you must complete. Do not
summarize or pre-parse it — implement exactly what it asks for.

---
{issue_text}
---

## Working Directory

The target repository has already been cloned into:

    /workspace

All work — reading files, making changes, running tests — happens inside that
directory unless you have a reason to go elsewhere.

## What You Must Do

You own the entire rest of the workflow. Specifically:

1. **Analyze** the repository and the issue text to understand what is being
   requested and what languages, tools, and runtime versions are required.
2. **Install** anything the generic sandbox image does not already provide.
   You have network access to PyPI, npm, Go module proxies, and common apt
   mirrors — use them freely.
3. **Implement** the requested change.
4. **Verify** your work (run the project's existing tests, or write new ones if
   the project has none, or at minimum confirm the code runs without errors).
5. **Commit and push** your changes to a new branch. Use the git credentials
   available in your environment. If the issue text does not specify a branch
   name, create one with the pattern `Jiffy/<short-description-of-change>`.
6. **Open a Pull Request** via the provider's tooling *only if the issue text
   explicitly asks you to open one*.
7. **Code review mention** — *only if the issue text explicitly asks for a code
   review* — include a mention of the configured code-review bot handle in the
   PR description (if you opened a PR) or in your final result summary (if you
   did not).
8. **Callback attempt** — *after* completing the above (whether you succeeded
   or partially succeeded), you MUST attempt to call the callback endpoint to
   report your result. See "Callback Delivery" below for details.

## Callback Delivery

You MUST attempt exactly ONE call to the callback endpoint after finishing
your work. Use the following callback spec to build the request:

```json
{spec_json}
```

Using this spec:
- **URL**: {callback_url}
- **Method**: from the spec's `method` field.
- **Auth header**: set `{spec['auth_header']}` to `{spec['auth_value_prefix']}` + the callback secret below.
- **Callback secret**: {callback_secret}
- **Body**: a JSON object containing the fields listed in the spec's `body_fields` (include `task_id` as your own integer task ID — report it as 0 if you don't have one, but prefer the callback to include a real value).
- **Query**: include any fields listed in `query_fields` as query parameters.
- **Headers**: include any fields listed in `header_fields` as additional headers.

The callback URL and secret are provided above. Make exactly ONE attempt — do
not retry. If the call fails, report that in your result's `callback` object.

## Required Final Output

When you are finished — whether you succeeded or failed — you MUST produce a
JSON file at the following path:

    {AGENT_RESULT_PATH}

The file must contain exactly one JSON object with these fields:

| Field                  | Type     | Required | Description |
|------------------------|----------|----------|-------------|
| `status`               | string   | yes      | `"done"` if the task was completed, `"failed"` if it was not. |
| `branch_name`          | string   | yes      | The branch you created or worked on. |
| `branch_base`          | string   | yes      | The branch you created new branch from. |
| `pr_url`               | string   | no       | URL of the PR/MR you opened, if any. |
| `programming_language` | string   | no       | Best-effort detection of the primary language used (e.g. `"python"`, `"typescript"`). |
| `summary`              | string   | yes      | A brief summary of what you did. |
| `error_message`        | string   | no       | Details of what went wrong, if `status` is `"failed"`. |
| `callback`             | object   | yes      | Outcome of your callback attempt. See below. |

### The `callback` object

| Field       | Type    | Required | Description |
|-------------|---------|----------|-------------|
| `attempted` | boolean | yes      | Whether you attempted the callback call. |
| `succeeded` | boolean | yes      | Whether the callback was delivered successfully (HTTP < 300). |
| `error`     | string  | no       | Error message if the callback attempt failed. |

Example:
```json
{{"attempted": true, "succeeded": true, "error": null}}
```

If you could not attempt the callback at all (e.g. network unavailable), set
`attempted` to false and explain why in `error`.

Do not skip this step. If you do not produce this file the system will treat
your run as a failure.
"""


def read_agent_result(container: Container) -> AgentResult:
    """Read and parse the structured result the agent was required to produce.

    If the result file is missing or malformed, returns a failed AgentResult
    rather than raising — the caller treats any non-"done" status the same way.
    """
    def _parse_callback(data: dict) -> dict:
        """Extract and validate the callback sub-object from agent result data."""
        raw = data.get("callback")
        if not isinstance(raw, dict):
            return {"attempted": False, "succeeded": False, "error": "Missing or invalid 'callback' object in agent result"}
        return {
            "attempted": bool(raw.get("attempted", False)),
            "succeeded": bool(raw.get("succeeded", False)),
            "error": raw.get("error") if isinstance(raw.get("error"), str) else None,
        }

    def _make_result(
        status: str = "failed",
        branch_name: str | None = None,
        pr_url: str | None = None,
        programming_language: str | None = None,
        summary: str | None = None,
        error_message: str | None = None,
        model: str | None = None,
        callback: dict | None = None,
    ) -> AgentResult:
        return AgentResult(
            status=status,
            branch_name=branch_name,
            pr_url=pr_url,
            programming_language=programming_language,
            summary=summary,
            error_message=error_message,
            model=model,
            callback=callback or {"attempted": False, "succeeded": False, "error": "No callback information available"},
        )

    try:
        exit_code, (output, _) = container.exec_run(
            cmd=["cat", AGENT_RESULT_PATH], demux=True
        )
        if exit_code != 0:
            return _make_result(
                status="failed",
                error_message=(
                    f"Agent result file not found at {AGENT_RESULT_PATH} "
                    f"(exit code {exit_code}). The agent did not produce the "
                    "required output contract."
                ),
            )

        result_data = json.loads(output)
        callback = _parse_callback(result_data)
        status = result_data.get("status")
        if status not in ("done", "failed"):
            return _make_result(
                status="failed",
                branch_name=result_data.get("branch_name"),
                pr_url=result_data.get("pr_url"),
                programming_language=result_data.get("programming_language"),
                summary=result_data.get("summary"),
                error_message=(
                    result_data.get("error_message")
                    or "Agent result JSON is missing a valid 'status' field (must be 'done' or 'failed')."
                ),
                model=result_data.get("model"),
                callback=callback,
            )
        return _make_result(
            status=status,
            branch_name=result_data.get("branch_name"),
            pr_url=result_data.get("pr_url"),
            programming_language=result_data.get("programming_language"),
            summary=result_data.get("summary"),
            error_message=result_data.get("error_message"),
            model=result_data.get("model"),
            callback=callback,
        )
    except json.JSONDecodeError as e:
        return _make_result(
            status="failed",
            error_message=f"Agent result file is not valid JSON: {e}",
        )
    except Exception as e:
        logger.exception(
            "Unexpected error reading agent result from container %s",
            container.short_id,
        )
        return _make_result(
            status="failed",
            error_message=f"Failed to read agent result: {e}",
        )
