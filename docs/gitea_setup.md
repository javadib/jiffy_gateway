# Gitea Setup — Jiffy Integration

## 1. Overview

Gitea Actions supports workflow files similar to GitHub Actions, placed in
`.gitea/workflows/`. The edge component lives in
`.gitea/workflows/jiffy.yml`, triggered on issue and issue-comment events.

The same "no noise reaching the central server" principle applies: the
workflow checks for the `@jiffy` mention before doing anything else, then
collects the full issue thread and posts it to the Jiffy Gateway's ingestion
endpoint.

## 2. File Location

Place the workflow file at:

```
<repo-root>/.gitea/workflows/jiffy.yml
```

Gitea Actions reads workflow files from `.gitea/workflows/` (not
`.github/workflows/`). This path is the Gitea-native equivalent of GitHub's
`.github/workflows/`.

## 3. Trigger Events and Mention Check

### Trigger Events

```yaml
on:
  issues:
    types: [opened]
  issue_comment:
    types: [created]
```

This matches the GitHub workflow: capture new issues and new comments on
existing issues.

### Mention Check

Gitea Actions supports `if:` conditions with `contains()` on event context:

```yaml
if: |
  (github.event_name == 'issues' && contains(github.event.issue.body, '@jiffy')) ||
  (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@jiffy'))
```

This is functionally identical to GitHub Actions syntax. No special handling
needed here.

### Key Difference from GitHub Actions

Gitea Actions does **not** support `actions/github-script@v9` or any of the
`actions/*` JavaScript-based actions. Those are GitHub-specific actions that
run inside the GitHub-hosted Actions runtime. Gitea runners cannot execute
them.

The equivalent functionality — fetching the issue, listing comments, and
assembling the payload — must be implemented using **bash + curl** against
the Gitea API directly.

## 4. Collecting the Full Issue Thread

### Via the Gitea API (recommended)

Use `curl` to fetch the issue and its comments from the Gitea REST API:

```bash
# Fetch issue details
curl -s -H "Authorization: token $GITEA_TOKEN" \
  "$GITEA_URL/api/v1/repos/$OWNER/$REPO/issues/$ISSUE_NUMBER"

# Fetch comments
curl -s -H "Authorization: token $GITEA_TOKEN" \
  "$GITEA_URL/api/v1/repos/$OWNER/$REPO/issues/$ISSUE_NUMBER/comments"
```

The Gitea API returns JSON. You can combine them with `jq`:

```bash
issue_body=$(curl -s -H "Authorization: token $GITEA_TOKEN" \
  "$GITEA_URL/api/v1/repos/$OWNER/$REPO/issues/$ISSUE_NUMBER" | jq -r '.body')

comments=$(curl -s -H "Authorization: token $GITEA_TOKEN" \
  "$GITEA_URL/api/v1/repos/$OWNER/$REPO/issues/$ISSUE_NUMBER/comments" | jq -r '.[].body')

full_thread="$issue_body"
while IFS= read -r comment; do
  full_thread="$full_thread --- $comment"
done <<< "$comments"
```

### Via `tea` CLI (alternative)

[tea](https://gitea.com/gitea/tea) is Gitea's official CLI, analogous to
GitHub's `gh`. If installed in the runner, you can use:

```bash
tea issue show --repo "$OWNER/$REPO" "$ISSUE_NUMBER" --output json
```

However, `tea` is not pre-installed on most Gitea runner images. The curl
approach is more portable and does not add a dependency.

## 5. Payload Shape

The Jiffy Gateway expects this exact JSON payload when POSTed to
`/api/gitea/ingestion`:

| Field | Type | Description |
|---|---|---|
| `repo.url` | string | Git remote URL, e.g. `https://gitea.example.com/owner/repo.git` |
| `repo.token` | string | Gitea access token for clone, push, and PR creation |
| `repo.username` | string | The Git username / token owner (required for Gitea, unlike GitHub where it is ignored) |
| `issue.text` | string | Full thread: issue body + all comments joined by ` --- ` separators |
| `issue.external_issue_id` | string | The issue number as a string (e.g. `"42"`) |
| `callback.url` | string | URL where the Gateway will POST the final result. For posting back as an issue comment, use: `https://<gitea-host>/api/v1/repos/<owner>/<repo>/issues/<number>/comments` |
| `callback.secret` | string | Opaque token forwarded verbatim in the callback's `Authorization: Bearer <secret>` header. Typically set to the same Gitea access token so the callback can authenticate against the Gitea API. |

### Building `callback.url`

Since the Gateway's callback dispatches results to `callback.url`, you point
it at the Gitea issue-comments API endpoint so the callback posts the result
as a comment on the issue:

```
https://<gitea-host>/api/v1/repos/<owner>/<repo>/issues/<issue-number>/comments
```

The `callback.secret` should be the same Gitea access token — it will be
sent as `Authorization: Bearer <token>` by the Gateway when it POSTs the
result.

## 6. Required Secrets

| Secret name | Description |
|---|---|
| `GITEA_ACCESS_TOKEN` | A Gitea access token with `repo` and `issue` scopes. Must have a **long expiry** (set to 1 year or no expiry) — short-lived tokens will break push and callback operations mid-job. |
| `JIFFY_GITEA_INGEST_TOKEN` | The shared `X_JIFFY_TOKEN` secret for the Gitea provider. This must match the value of the `GITEA_INGEST_TOKEN` environment variable on the Gateway. Generate with `openssl rand -hex 32`. |
| `JIFFY_GATEWAY_URL` | The base URL of the Jiffy Gateway, e.g. `https://jiffy-gateway.example.com` |

These are stored as **Gitea Actions secrets** in the repository settings
(Settings → Actions → Secrets).

## 7. Complete Example Workflow

```yaml
name: Jiffy Dispatch

on:
  issues:
    types: [opened]
  issue_comment:
    types: [created]

jobs:
  dispatch:
    runs-on: ubuntu-latest
    if: |
      (github.event_name == 'issues' && contains(github.event.issue.body, '@jiffy')) ||
      (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@jiffy'))
    steps:
      - name: Collect thread and dispatch to Jiffy Gateway
        env:
          GITEA_TOKEN: ${{ secrets.GITEA_ACCESS_TOKEN }}
          JIFFY_URL: ${{ secrets.JIFFY_GATEWAY_URL }}
          JIFFY_INGEST_TOKEN: ${{ secrets.JIFFY_GITEA_INGEST_TOKEN }}
          GITEA_HOST: ${{ github.server_url }}
          OWNER: ${{ github.repository_owner }}
          REPO: ${{ github.event.repository.name }}
          ISSUE_NUMBER: ${{ github.event.issue.number }}
          ACTOR: ${{ github.actor }}
        run: |
          set -e

          # Fetch issue body
          ISSUE_JSON=$(curl -s -H "Authorization: token $GITEA_TOKEN" \
            "${GITEA_HOST}/api/v1/repos/${OWNER}/${REPO}/issues/${ISSUE_NUMBER}")

          ISSUE_BODY=$(echo "$ISSUE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('body',''))")

          # Fetch and concatenate comments
          COMMENTS_JSON=$(curl -s -H "Authorization: token $GITEA_TOKEN" \
            "${GITEA_HOST}/api/v1/repos/${OWNER}/${REPO}/issues/${ISSUE_NUMBER}/comments")

          FULL_TEXT=$(python3 -c "
        import sys, json
        data = json.load(sys.stdin)
        body = data['issue_body']
        comments = data['comments']
        parts = [body] + [c.get('body','') for c in comments]
        print(' --- '.join(parts))
        " <<< "{\"issue_body\": $(echo "$ISSUE_BODY" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))"), \"comments\": $(echo "$COMMENTS_JSON")}")

          # Build repo URL
          REPO_URL="${GITEA_HOST}/${OWNER}/${REPO}.git"

          # Build callback URL (Gitea issue comment endpoint)
          CALLBACK_URL="${GITEA_HOST}/api/v1/repos/${OWNER}/${REPO}/issues/${ISSUE_NUMBER}/comments"

          # Build payload
          PAYLOAD=$(python3 -c "
        import json
        payload = {
            'repo': {
                'url': '$REPO_URL',
                'token': '$GITEA_TOKEN',
                'username': '$ACTOR'
            },
            'issue': {
                'text': '''$FULL_TEXT''',
                'external_issue_id': '$ISSUE_NUMBER'
            },
            'callback': {
                'url': '$CALLBACK_URL',
                'secret': '$GITEA_TOKEN'
            }
        }
        print(json.dumps(payload))
        ")

          # Dispatch to Jiffy Gateway
          HTTP_STATUS=$(curl -s -o /tmp/jiffy_response.txt -w "%{http_code}" \
            -X POST "${JIFFY_URL}/api/gitea/ingestion" \
            -H "Content-Type: application/json" \
            -H "X-Jiffy-Token: ${JIFFY_INGEST_TOKEN}" \
            -d "$PAYLOAD")

          echo "Jiffy ingestion HTTP status: $HTTP_STATUS"
          cat /tmp/jiffy_response.txt

          if [ "$HTTP_STATUS" -ge 400 ]; then
            echo "Jiffy ingestion failed with status $HTTP_STATUS"
            exit 1
          fi
```

### Important: Avoiding `${{ }}` Interpolation Issues

The example above avoids interpolating `${{ secrets.GITEA_ACCESS_TOKEN }}`
or `${{ github.event.issue.number }}` directly into shell command strings.
Instead, every value is passed through `env:` and accessed as environment
variables (`$GITEA_TOKEN`, `$ISSUE_NUMBER`, etc.).

This prevents a class of bugs where special characters in context values or
secrets (e.g. `${{ }}` in issue text, or tokens containing shell metacharacters)
can break the shell command or leak secrets via error messages.

The payload is also constructed inside Python (via `-c`), not via shell
variable interpolation, to further isolate secrets from shell parsing.

## 8. Common Pitfalls

### Token Expiry

The single most common failure: a Gitea access token with a short expiry
(e.g. 30 days) expires while the Gateway still holds the payload in Redis
(the payload TTL is 4 hours, but if retries or queue delays push execution
past token expiry, the job will fail).

- Set the token to **1 year or no expiry**.
- Put a reminder in your calendar to rotate the token before it expires.

This is the same lesson learned from GitHub — short-lived PATs cause hard-
to-diagnose failures in the clone and push steps.

### Raw `${{ }}` Interpolation in Shell Commands

Do not write:

```yaml
run: curl -H "Authorization: token ${{ secrets.GITEA_ACCESS_TOKEN }}" ...
```

This interpolates the raw token value into the shell string. If the token
contains `$`, `` ` ``, `\`, or other shell metacharacters, the command can
break or behave unexpectedly. It also makes the token visible in step logs
if the runner echoes commands.

**Always pass secrets through `env:` and reference them as environment
variables in shell steps.**

### Gitea Actions–Specific Quirks

- **No `actions/*` JavaScript actions.** Any workflow that depends on
  `actions/github-script`, `actions/checkout`, `actions/upload-artifact`,
  etc. will fail on a Gitea runner. The Gitea runner ecosystem is
  fundamentally different — it runs containers (via `act`) rather than
  JavaScript in Node. All logic must be implemented with shell scripts, or
  by running containers via `uses: docker://`.

- **`github.server_url` vs Gitea's host.** In the Gitea Actions context,
  `github.server_url` is set by the runner to the Gitea instance URL
  (e.g. `https://gitea.example.com`), not `https://github.com`. This is a
  Gitea Actions compatibility feature, not a bug. Use it directly.

- **`github.event.repository.name`** is populated by Gitea Actions and
  contains the repository name (e.g. `my-project`). The full name (e.g.
  `owner/my-project`) is in `github.event.repository.full_name`.

- **`github.repository_owner`** is set to the repository owner/org name.

- **Python is not guaranteed on all runners.** The example above uses
  `python3` for JSON construction. If your runner image does not include
  Python, install it or use `jq` for JSON construction instead. The
  `ubuntu-latest` runner image typically includes Python.

- **`jq` availability.** `jq` is available on most runner images. If
  `python3` is unavailable, rewrite the JSON construction with `jq`:

  ```bash
  ISSUE_BODY=$(echo "$ISSUE_JSON" | jq -r '.body // empty')
  COMMENTS_BODY=$(echo "$COMMENTS_JSON" | jq -r '[.[].body // empty] | join(" --- ")')
  FULL_TEXT="${ISSUE_BODY} --- ${COMMENTS_BODY}"
  PAYLOAD=$(jq -n \
    --arg url "$REPO_URL" \
    --arg token "$GITEA_TOKEN" \
    --arg username "$ACTOR" \
    --arg text "$FULL_TEXT" \
    --arg issue_id "$ISSUE_NUMBER" \
    --arg callback_url "$CALLBACK_URL" \
    --arg callback_secret "$GITEA_TOKEN" \
    '{repo: {url: $url, token: $token, username: $username}, issue: {text: $text, external_issue_id: $issue_id}, callback: {url: $callback_url, secret: $callback_secret}}')
  ```

- **`tea` CLI is not pre-installed.** Do not rely on `tea` being available
  on the runner. The curl-based approach works universally.

- **Docker-in-Docker limitations.** If your workflow needs to run containers,
  be aware that Gitea runners typically run inside containers themselves, so
  Docker-in-Docker (DinD) requires explicit setup (`privileged: true` or
  `volume: /var/run/docker.sock`). This does not apply to the Jiffy edge
  workflow since it only makes HTTP calls.

- **Secrets masking.** Gitea Actions masks secrets in log output, similar to
  GitHub Actions. Values passed via `env:` are automatically masked. However,
  avoid echo'ing or printing them directly in shell steps.
