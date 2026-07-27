# Setting Up Jiffy for a GitLab Project

This guide explains how to configure Jiffy for a GitLab project so that
mentioning `@jiffy` on an Issue triggers a task on your Jiffy Gateway.

---

## 1. Architecture overview

Unlike GitHub Actions (which can trigger directly on issue and comment
events), GitLab CI does not natively trigger on issue/comment mentions.
A lightweight webhook relay is required as an intermediary.

The flow is:

```
GitLab webhook (Issues events / Comments) ──▶ Relay service
                                                    │
                                                    ├── verify X-Gitlab-Token
                                                    ├── check for @jiffy mention
                                                    ├── fetch full thread via GitLab API
                                                    └── POST payload to Jiffy Gateway
                                                         /api/gitlab/ingestion
```

The relay is the **only** component required on the GitLab side besides
the webhook itself. There is no GitLab CI/CD pipeline, `.gitlab-ci.yml`
job, or Pipeline Trigger API involved — the relay handles detection,
thread collection, and dispatch in a single step.

---

## 2. Setting up the relay

### Role

The relay is a small standalone HTTP service that:

1. Receives incoming webhooks from GitLab (issue and note events).
2. Verifies the webhook using the configured `X-Gitlab-Token` secret.
3. Checks whether the issue body or the new comment body contains
   `@jiffy`.
4. If the mention is found, fetches the full issue description and all
   notes/comments via the GitLab API.
5. Concatenates the thread into a single text block.
6. Builds the Jiffy ingestion payload and POSTs it to the Gateway.

### Deployment

The relay has no database, no queue, and no persistent state. It can run
as a simple process on any machine with network access to both the GitLab
instance and the Jiffy Gateway — a small VM, a Docker container, or a
serverless function. Place it near the GitLab instance to minimise latency.

### Required environment variables

| Variable | Description |
|----------|-------------|
| `GITLAB_WEBHOOK_SECRET` | Shared secret configured in the GitLab webhook settings. The relay compares this against the `X-Gitlab-Token` header on incoming webhooks. |
| `GITLAB_API_TOKEN` | A GitLab Personal Access Token (or project access token) with `read_api` scope. Used to fetch the issue description and all notes/comments. This same token is also used as the `callback.secret` in the ingestion payload. |
| `CI_API_V4_URL` | Base URL of the GitLab API, e.g. `https://gitlab.com/api/v4` for GitLab SaaS, or `https://gitlab.example.com/api/v4` for a self-managed instance. |
| `JIFFY_GATEWAY_URL` | Base URL of your Jiffy Gateway instance, e.g. `https://jiffy.example.com`. |
| `JIFFY_INGEST_TOKEN` | The shared ingestion secret for the GitLab provider. Must match the `GITLAB_INGEST_TOKEN` configured on the Gateway. |

---

## 3. GitLab project configuration

### Webhook

In your GitLab project, go to **Settings > Webhooks** and add a new webhook:

- **URL**: the URL of your relay service, e.g. `https://relay.example.com/webhook`.
- **Secret Token**: the same value as `GITLAB_WEBHOOK_SECRET`.
- **Trigger**: enable **Issues events** and **Comments**.
- Leave all other options at their defaults.

GitLab will now send a POST request to the relay every time an issue is
created or updated, and every time a comment (note) is added.

### No CI/CD variables or Pipeline Triggers

This setup does **not** require any CI/CD variables, pipeline triggers,
or a `.gitlab-ci.yml` file. The relay communicates directly with the
Jiffy Gateway via HTTP. This is a simplification compared to a
pipeline-based approach — there is no intermediate CI job, no trigger
token, and no additional runner dependency.

---

## 4. Payload shape expected by the Gateway

The relay sends a POST request to the Gateway's ingestion endpoint
(`POST /api/gitlab/ingestion`) with the following JSON body:

```json
{
  "repo": {
    "url": "https://gitlab.com/group/project.git",
    "token": "glpat-...",
    "username": "gitlab-user"
  },
  "issue": {
    "text": "Issue description --- comment 1 text --- comment 2 text",
    "external_issue_id": "42"
  },
  "callback": {
    "url": "https://gitlab.com/api/v4/projects/17/issues/42/notes",
    "secret": "glpat-..."
  }
}
```

### Field explanations

| Field | Description |
|-------|-------------|
| `repo.url` | Full clone URL of the GitLab project. |
| `repo.token` | A GitLab access token (PAT or project token) with scope to clone, push, and open merge requests. |
| `repo.username` | A GitLab username used for authenticated git operations (required by GitLab's `https://user:token@host` format). |
| `issue.text` | The full issue thread text — issue description + all notes/comments — joined together. The Gateway passes this verbatim to the coding agent. |
| `issue.external_issue_id` | The issue IID (internal ID within the project) as a string. Used for deduplication and callback correlation. |
| `callback.url` | URL where the Gateway will POST the result. For GitLab this is the Issue Notes API endpoint, which posts the result back as a comment on the original issue. For self-managed GitLab, adjust the host. |
| `callback.secret` | Opaque secret that the Gateway forwards unchanged in the callback request. The receiving endpoint uses it to verify the callback is authentic. In this setup, the `GITLAB_API_TOKEN` is used, since the GitLab Issue Notes API requires it for authentication. |

### Building `callback.url`

For GitLab SaaS (`gitlab.com`):

```
https://gitlab.com/api/v4/projects/{PROJECT_ID}/issues/{ISSUE_IID}/notes
```

For self-managed GitLab, replace the host:

```
https://gitlab.example.com/api/v4/projects/{PROJECT_ID}/issues/{ISSUE_IID}/notes
```

The `PROJECT_ID` is GitLab's numeric project ID (found in the project
settings or returned by the API). The `ISSUE_IID` is the issue's internal
ID (the number shown in the issue URL).

---

## 5. Complete working example

### Relay script (Python)

Below is a complete relay script that you can deploy. It uses the standard
library and `requests` — install dependencies with `pip install requests`.

```python
import os
import json
import logging

import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

GITLAB_WEBHOOK_SECRET = os.environ["GITLAB_WEBHOOK_SECRET"]
GITLAB_API_TOKEN = os.environ["GITLAB_API_TOKEN"]
CI_API_V4_URL = os.environ["CI_API_V4_URL"].rstrip("/")
JIFFY_GATEWAY_URL = os.environ["JIFFY_GATEWAY_URL"].rstrip("/")
JIFFY_INGEST_TOKEN = os.environ["JIFFY_INGEST_TOKEN"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jiffy-relay")


class JiffyRelayHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Verify webhook secret
        token = self.headers.get("X-Gitlab-Token", "")
        if token != GITLAB_WEBHOOK_SECRET:
            self.send_error(401, "Invalid webhook token")
            return

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        event_type = self.headers.get("X-Gitlab-Event", "")
        if event_type not in ("Issue Hook", "Note Hook"):
            self.send_response(204)
            self.end_headers()
            return

        # Determine the issue IID and whether @jiffy is mentioned
        if event_type == "Issue Hook":
            issue_iid = data.get("object_attributes", {}).get("iid")
            issue_description = data.get("object_attributes", {}).get("description", "")
            content_to_check = issue_description
        elif event_type == "Note Hook":
            issue_iid = data.get("issue", {}).get("iid")
            note_body = data.get("object_attributes", {}).get("note", "")
            issue_description = data.get("issue", {}).get("description", "")
            content_to_check = note_body

        if not issue_iid:
            self.send_error(400, "Missing issue IID")
            return

        # Check for @jiffy mention
        if "@jiffy" not in content_to_check:
            self.send_response(204)
            self.end_headers()
            return

        # Fetch the full issue thread via GitLab API
        project_id = data.get("project_id") or data.get("project", {}).get("id")
        headers = {"Authorization": f"Bearer {GITLAB_API_TOKEN}"}

        # Fetch issue details
        issue_url = f"{CI_API_V4_URL}/projects/{project_id}/issues/{issue_iid}"
        issue_resp = requests.get(issue_url, headers=headers, timeout=30)
        issue_resp.raise_for_status()
        issue_data = issue_resp.json()
        issue_text = issue_data.get("description", "")

        # Fetch all notes (comments)
        notes_url = f"{CI_API_V4_URL}/projects/{project_id}/issues/{issue_iid}/notes"
        notes = []
        page = 1
        while True:
            notes_resp = requests.get(
                notes_url, headers=headers,
                params={"page": page, "per_page": 100},
                timeout=30,
            )
            notes_resp.raise_for_status()
            page_notes = notes_resp.json()
            if not page_notes:
                break
            notes.extend(page_notes)
            page += 1

        # Build the full thread text
        all_texts = [issue_text or ""]
        for note in notes:
            body = note.get("body", "")
            # Skip system notes (automated messages)
            if note.get("system", False):
                continue
            all_texts.append(body)

        full_text = " --- ".join(all_texts)

        # Build repo info
        repo_url = issue_data.get("_links", {}).get("self", "")
        # Fallback: construct from project path
        project_path = issue_data.get("path_with_namespace", "")
        if not repo_url:
            repo_url = f"{CI_API_V4_URL.replace('/api/v4', '')}/{project_path}.git"
            repo_url = repo_url.replace("https://", f"https://{issue_data.get('author', {}).get('username', 'root')}:{GITLAB_API_TOKEN}@")

        # Build callback URL for posting result as a note
        callback_url = f"{CI_API_V4_URL}/projects/{project_id}/issues/{issue_iid}/notes"

        # Build ingestion payload
        payload = {
            "repo": {
                "url": issue_data.get("_links", {}).get("self", "").replace("/api/v4/projects/", "/").split("/issues/")[0]
                       or f"{CI_API_V4_URL.replace('/api/v4', '')}/{project_path}.git",
                "token": GITLAB_API_TOKEN,
                "username": issue_data.get("author", {}).get("username", ""),
            },
            "issue": {
                "text": full_text,
                "external_issue_id": str(issue_iid),
            },
            "callback": {
                "url": callback_url,
                "secret": GITLAB_API_TOKEN,
            },
        }

        # Set proper repo URL
        host_base = CI_API_V4_URL.replace("/api/v4", "")
        payload["repo"]["url"] = f"{host_base}/{project_path}.git"

        # Dispatch to Jiffy Gateway
        ingest_url = f"{JIFFY_GATEWAY_URL}/api/gitlab/ingestion"
        ingest_headers = {
            "Content-Type": "application/json",
            "X-Jiffy-Token": JIFFY_INGEST_TOKEN,
        }

        try:
            response = requests.post(
                ingest_url,
                json=payload,
                headers=ingest_headers,
                timeout=30,
            )
            logger.info(
                "Dispatched issue %s (project %s) → %s (%d)",
                issue_iid, project_id, ingest_url, response.status_code,
            )
            if response.status_code >= 400:
                logger.error("Gateway returned %d: %s", response.status_code, response.text)
                self.send_error(502, f"Gateway returned {response.status_code}")
                return
        except requests.RequestException as e:
            logger.error("Failed to reach Gateway: %s", e)
            self.send_error(502, f"Gateway unreachable: {e}")
            return

        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "dispatched"}).encode())


def main():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), JiffyRelayHandler)
    logger.info("Jiffy relay listening on port %d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
```

### Running the relay

```bash
pip install requests
export GITLAB_WEBHOOK_SECRET=your-webhook-secret
export GITLAB_API_TOKEN=glpat-...
export CI_API_V4_URL=https://gitlab.com/api/v4
export JIFFY_GATEWAY_URL=https://jiffy.example.com
export JIFFY_INGEST_TOKEN=your-jiffy-ingest-token
python relay.py
```

For production, consider running the relay behind a process supervisor
(e.g. systemd, supervisord) or inside a Docker container with a restart
policy.

---

## 6. Generating the ingestion token

The `JIFFY_INGEST_TOKEN` (which the Gateway expects as the environment
variable `GITLAB_INGEST_TOKEN`) must be generated with a cryptographically
secure generator:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set this same value on both the Gateway (as `GITLAB_INGEST_TOKEN`) and
the relay (as `JIFFY_INGEST_TOKEN`).

---

## 7. Common pitfalls

### Token scope and expiry

`GITLAB_API_TOKEN` is used for two purposes: fetching the issue thread
via the GitLab API, and authenticating the callback (the Gateway uses it
to POST the result back as an issue note). It must therefore have at
least `read_api` scope (for the API reads) and `write_repository` scope
(for the Gateway to push branches and open merge requests). A Personal
Access Token (PAT) or a project access token with these scopes works best.

Set a reasonable expiry (30–90 days) and rotate before it expires. During
rotation, both the relay's `GITLAB_API_TOKEN` and the `repo.token` become
invalid simultaneously — update both and the callback will keep working.

### The relay as a single point of failure

Because the relay owns the entire GitLab-side flow (webhook reception,
mention detection, thread collection, dispatch), if the relay is down,
all `@jiffy` mentions are silently ignored. Monitor the relay's health
and consider running it with a process supervisor or behind a load
balancer if uptime is critical.

### Keeping secrets separate

The `GITLAB_WEBHOOK_SECRET` (verified against the `X-Gitlab-Token`
header), the `GITLAB_API_TOKEN` (used for API calls and as callback
secret), and the `JIFFY_INGEST_TOKEN` (used to authenticate against the
Gateway) are three distinct credentials with different roles. Do not
reuse them. If one is compromised, rotate only that one without affecting
the others.

### Project ID vs. issue IID

GitLab uses two numeric identifiers: the **project ID** (a global,
project-scoped integer) and the **issue IID** (an internal issue number
scoped to the project). The webhook payload includes both. The relay uses
the project ID for API URLs (path-based references also work) and the
issue IID for the `external_issue_id` field and callback URL construction.
