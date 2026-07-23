"""Handles agent instructions and result parsing."""
import json
import logging
from typing import Any, Dict, NamedTuple

from docker.models.containers import Container

from .exceptions import AgentError

logger = logging.getLogger(__name__)

AGENT_RESULT_PATH = "/workspace/.jiffy_result.json"


class AgentResult(NamedTuple):
    status: str
    branch_name: str | None
    pr_url: str | None
    programming_language: str | None
    summary: str | None
    error_message: str | None


def build_agent_instructions(payload: Dict[str, Any]) -> str:
    """Build the instructions text handed to the coding agent.

    The instructions are agent-agnostic — they describe the contract without
    assuming any particular CLI conventions.
    """
    issue_text = payload.get("issue", {}).get("text", "")

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

## Required Final Output

When you are finished — whether you succeeded or failed — you MUST produce a
JSON file at the following path:

    {AGENT_RESULT_PATH}

The file must contain exactly one JSON object with these fields:

| Field                | Type     | Required | Description |
|----------------------|----------|----------|-------------|
| `status`             | string   | yes      | `"done"` if the task was completed, `"failed"` if it was not. |
| `branch_name`        | string   | yes      | The branch you created or worked on. |
| `pr_url`             | string   | no       | URL of the PR/MR you opened, if any. |
| `programming_language` | string | no       | Best-effort detection of the primary language used (e.g. `"python"`, `"typescript"`). |
| `summary`            | string   | yes      | A brief summary of what you did. |
| `error_message`      | string   | no       | Details of what went wrong, if `status` is `"failed"`. |

Do not skip this step. If you do not produce this file the system will treat
your run as a failure.
"""


def read_agent_result(container: Container) -> AgentResult:
    """Read and parse the structured result the agent was required to produce.

    If the result file is missing or malformed, returns a failed AgentResult
    rather than raising — the caller treats any non-"done" status the same way.
    """
    try:
        exit_code, (output, _) = container.exec_run(f"cat {AGENT_RESULT_PATH}")
        if exit_code != 0:
            return AgentResult(
                status="failed",
                branch_name=None,
                pr_url=None,
                programming_language=None,
                summary=None,
                error_message=(
                    f"Agent result file not found at {AGENT_RESULT_PATH} "
                    f"(exit code {exit_code}). The agent did not produce the "
                    "required output contract."
                ),
            )

        result_data = json.loads(output)
        status = result_data.get("status")
        if status not in ("done", "failed"):
            return AgentResult(
                status="failed",
                branch_name=result_data.get("branch_name"),
                pr_url=result_data.get("pr_url"),
                programming_language=result_data.get("programming_language"),
                summary=result_data.get("summary"),
                error_message=(
                    result_data.get("error_message")
                    or "Agent result JSON is missing a valid 'status' field (must be 'done' or 'failed')."
                ),
            )
        return AgentResult(
            status=status,
            branch_name=result_data.get("branch_name"),
            pr_url=result_data.get("pr_url"),
            programming_language=result_data.get("programming_language"),
            summary=result_data.get("summary"),
            error_message=result_data.get("error_message"),
        )
    except json.JSONDecodeError as e:
        return AgentResult(
            status="failed",
            branch_name=None,
            pr_url=None,
            programming_language=None,
            summary=None,
            error_message=f"Agent result file is not valid JSON: {e}",
        )
    except Exception as e:
        logger.exception(
            "Unexpected error reading agent result from container %s",
            container.short_id,
        )
        return AgentResult(
            status="failed",
            branch_name=None,
            pr_url=None,
            programming_language=None,
            summary=None,
            error_message=f"Failed to read agent result: {e}",
        )
