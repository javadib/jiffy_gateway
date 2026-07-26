This file gives the technical context needed to work on the **Jiffy central server** codebase. Read this before making architectural decisions, adding dependencies, or generating code.

## What This Project Is

The central server for Jiffy: it receives pre-filtered task requests (full Issue/thread history) from lightweight edge components (GitHub Actions, GitLab CI jobs, Gitea Actions), provisions an isolated sandbox container, clones the target repository into it, and hands the entire rest of the work — understanding the request, installing whatever it needs, implementing the change, verifying it, committing, pushing, optionally opening a PR, and optionally mentioning a code-review bot — to a coding agent running inside that container. The Gateway itself does not parse requirements, decide on languages/versions, or orchestrate commit/push/PR as separate steps; its own responsibility is deliberately thin: authenticate the request, provision the sandbox, clone the repo, hand off to the agent with clear instructions, capture the agent's final result, and report it via a callback URL.

The edge components (mention detection, thread collection) are **out of scope** for this codebase — they live in separate repos/workflows. This project only implements the receiving endpoint, container provisioning + clone, the agent hand-off, and the callback reporter.

## End-to-End Flow (Authoritative)

This is the full sequence once a request reaches the Gateway. Only steps 1–3 and 6–7 are Gateway-orchestrated code; step 4 (analysis through PR) happens entirely inside the agent's own execution and is not broken into separate Gateway-tracked phases.

1. **Receive** the whole request body (the Issue/thread) from the edge component / git server via the ingestion endpoint.
2. **Provision the generic sandbox container** (script-driven) — always the same image (see Generic Sandbox Image below), regardless of what the task turns out to need.
3. **Clone** the repo into the container (script-driven, using the injected repo token). This is the last thing the Gateway's own code decides — everything from here on is the agent's responsibility.
4. **Hand off to the agent**, passing it the raw issue/thread text (verbatim, no pre-parsing) and the path to the cloned source, along with the instruction contract described in Agent Instruction Contract below. From this point, the agent:
   - analyzes the issue and the repository to determine what's actually being asked, and what languages/tools/runtime versions it needs;
   - installs anything the generic image doesn't already have;
   - implements the requested change;
   - verifies its own work;
   - commits and pushes using the repo token available to it in the environment;
   - opens a PR via the provider's tooling if the issue text asked for one;
   - includes a mention of the code-review bot (in the PR description if a PR was opened, or in its final result summary if not) if the issue text asked for a review — see Code Review Loop.
5. **Agent emits a final structured result** per the Agent Instruction Contract (status, branch name, PR url if any, summary, error message if failed).
6. **Container exits**; the worker parses that structured result.
7. **Post final report** to `callback_url` — status, summary (including the code-review bot mention if the agent included one), branch name, PR url (if any), or `error_message` (if failed).

There is no Gateway-side `pr_request`/`code_review_request` extraction step, no Gateway-side language/version detection, and no Gateway-side commit/push/PR-opening code — all of that is the agent's job, driven entirely by how it reads the issue text and the repository. The `Task` model may still store `branch_name`, `pr_url`, `programming_language`, etc., but only as **audit/reporting fields populated from the agent's own final output after the fact** — never pre-computed by the Gateway before or during the run.

**No fail-fast before container start for language/runtime.** Since analysis and installation are entirely the agent's responsibility, there is no pre-container check for "is this language supported." If the agent cannot meet the task's environment requirements for any reason, that surfaces only as a failure in its own final result, reported as `status="failed"` via the normal callback path.

## Tech Stack

- **Language / Framework**: Python, Django
- **Task queue**: Celery
- **Broker / result backend**: Redis
- **Database**: Django ORM, backed by **SQLite** initially
- **Code execution**: Docker (one container per job, ephemeral, resource-limited)
- **Coding agent**: Claude Code (or equivalent CLI agent) run inside the container

## Database Strategy — Important

- Use **Django ORM exclusively** for all data access. **Never write raw SQL, raw connection queries, or SQLite-specific syntax** (e.g. no `sqlite3` module calls, no vendor-specific SQL functions, no `PRAGMA` statements in application code).
- Do not use SQLite-only features: no JSON1-specific raw queries, no `AUTOINCREMENT` assumptions, no filename/path-based tricks. Stick to what `django.db.models` exposes portably.
- Avoid multi-database-specific quirks: don't rely on SQLite's loose type affinity (e.g. don't store a list in a `CharField` and expect querying by type). Use proper Django field types (`JSONField`, `CharField`, `ForeignKey`, etc.) — Django's `JSONField` works on both SQLite (3.9+) and PostgreSQL.
- Avoid heavy concurrent writes to the same rows from multiple Celery workers. SQLite serializes writes at the file level; keep write transactions short and avoid long-held locks (e.g. don't hold a DB transaction open while waiting on a Docker container or a network call).
- Use Django migrations for every schema change — never hand-edit the SQLite file.
- **Reasoning**: the project must be able to switch to PostgreSQL later by changing only `DATABASES` in settings (plus installing `psycopg`), with no application code changes. Any code that would break on that switch is a bug.

```python
# settings.py — structure so switching is a config-only change later
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
# Later, for Postgres:
# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.postgresql",
#         "NAME": os.environ["DB_NAME"],
#         "USER": os.environ["DB_USER"],
#         "PASSWORD": os.environ["DB_PASSWORD"],
#         "HOST": os.environ["DB_HOST"],
#         "PORT": os.environ["DB_PORT"],
#     }
# }
```

## Data Model (core apps)

Keep the relational DB for **durable metadata only** (status, audit trail, links). Heavy/ephemeral data (full thread payloads, repo access token) goes to Redis, not the DB.

```python
class Task(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("provisioning", "Provisioning sandbox"),
        ("cloning", "Cloning"),
        ("running", "Running"),   # covers the agent's entire analysis → implement → verify → commit/push → PR sequence
        ("reporting", "Reporting"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    provider = models.CharField(max_length=20)   # "github" | "gitlab" | "gitea"
    repo_url = models.CharField(max_length=500)
    issue_external_id = models.CharField(max_length=100)
    programming_language = models.CharField(max_length=50, null=True, blank=True)  # populated from the agent's final
                                                                                    # result, for audit/reporting only
    branch_name = models.CharField(max_length=255, null=True, blank=True)  # populated from the agent's final result
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    callback_url = models.URLField()
    callback_secret = models.CharField(max_length=1024)
    pr_url = models.URLField(null=True, blank=True)  # populated from the agent's final result, if it opened one
    error_message = models.TextField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

The full thread/payload (all Issue comments, metadata) **and the repo access token** are stored in Redis under `jiffy:task:{task_id}:payload` with a **short TTL** (a few hours, not days — see Payload & Token Handling below), written at ingestion time and read once by the worker. Neither the payload nor the token is ever persisted to the Django DB.

## Two Endpoints — Ingestion and Callback Dispatch

The Gateway exposes exactly two kinds of HTTP surfaces relevant to a task's lifecycle:

1. **Ingestion endpoints** (inbound, one per provider): `github/ingestion`, `gitlab/ingestion`, `gitea/ingestion`. Each receives the task request from that provider's edge component.
2. **Callback dispatch** (outbound): once the job finishes (success or failure), the Gateway calls `callback.url` (from the payload) with the signed result. The Gateway itself never posts comments back to the Issue — whatever handles that URL (the git server directly, or an intermediary) is responsible for that, using its own credentials.

The Gateway never holds or stores any chat/comment-posting credential. The repo access token it receives is used strictly for clone (by the Gateway) and then push/PR (by the agent, inside the container), for the lifetime of a single job, and is discarded afterward.

Note: `*/callback` (e.g. `github/callback`) is **not** an inbound HTTP route — it's internal module naming for the outbound callback-dispatch logic, kept per-provider for organizational consistency with the ingestion routes, not because the dispatch behavior actually differs by provider today.

## Ingestion Endpoint — Details

- Three separate Django views, one per provider (`github/ingestion`, `gitlab/ingestion`, `gitea/ingestion`) rather than one generic endpoint — kept distinct so each provider's auth secret and payload validation stay independently testable.
- **Auth**: a shared token read from the `X_JIFFY_TOKEN` request header, verified with a constant-time comparison (`hmac.compare_digest`) against that provider's own configured secret (e.g. `GITHUB_INGEST_TOKEN`, `GITLAB_INGEST_TOKEN`, `GITEA_INGEST_TOKEN` — one env var per provider/deployment, never shared across providers or across teams' installations). Reject with `401` before touching the DB or Redis if missing or mismatched.
- **Payload shape** (identical across all three providers):
  ```json
  {
    "repo": { "url": "", "token": "" },
    "issue": { "text": "", "issue_external_id": "" },
    "callback": { "url": "", "secret": "" }
  }
  ```
  A request missing any of the six leaf fields fails with a clear `400`, not a `KeyError`/`500`.
- On success:
  1. Create a `Task` row (`status="queued"`), with `repo_url` from `payload.repo.url` and `issue_external_id` from `payload.issue.issue_external_id`.
  2. Write the full payload (in this same nested shape) to Redis (`jiffy:task:{task.id}:payload`).
  3. Acquire the Redis lock (`jiffy:lock:issue:{provider}:{issue_external_id}`) before enqueueing, to guard against duplicate webhook deliveries — if already held, return `202` without creating a duplicate task.
  4. Enqueue `execute_task.delay(task.id)` on the `execute` Celery queue.
  5. Return `202 Accepted` immediately — do not block the HTTP response on job execution.

**Secret generation & rotation**: secrets are generated with a cryptographically secure generator (e.g. `openssl rand -hex 32`), unique per provider and per deployment, stored server-side via env var (`.env` with restrictive permissions, or a platform secrets manager for larger deployments) and edge-side via that provider's own CI/CD secrets store. Rotation is manual for phase one — no dual-secret (old+new simultaneously valid) support; a brief mismatch window during rotation is accepted.

## Celery Configuration

- **Single queue: `execute`.** There is no separate `ingest` queue — the edge components already do all mention-filtering before a request ever reaches the Gateway, so a dedicated pre-processing queue would be unused complexity given the expected volume. If a genuine need for lightweight async pre-processing emerges later, introduce the queue then, not preemptively.
- Given expected volume (~100–200 requests/day across all repos), keep worker concurrency low and explicit:

```bash
celery -A jiffy worker -Q execute --concurrency=3 -n execute@%h
```

- `acks_late=True` and `task_reject_on_worker_lost=True` on the execute task, so a killed worker re-queues the job instead of silently dropping it.
- **Retry policy (task execution)**: retry only on transient errors (network timeout, git push conflict, Docker daemon hiccup), **max 3 attempts, 1-minute interval between attempts**. Do **not** retry on logical/agent failures (e.g. agent couldn't complete the task) — mark `failed` and report immediately via callback.
- **Retry policy (callback dispatch)**: if `callback_url` is unreachable, retry **up to 3 times**. If all 3 attempts fail, log the failure clearly (the task remains `failed`/unreported from the edge's perspective) — do not silently drop it. There is currently no separate admin-alerting mechanism for a fully-failed callback; this is a known gap, not a design decision to revisit only if it becomes a real operational problem.
- Use a Redis-based lock (e.g. `redis.lock` with a key like `jiffy:lock:issue:{provider}:{issue_external_id}`) before enqueueing, to guard against duplicate webhook deliveries triggering the same task twice.

## Job Pipeline (inside the Celery task)

```python
@shared_task(bind=True, max_retries=3, acks_late=True)
def execute_task(self, task_id):
    task = Task.objects.get(id=task_id)
    payload = load_payload_from_redis(task_id)  # raises if missing/expired; includes repo token + raw thread text

    update_status(task, "provisioning")
    container = start_generic_sandbox_container(task)  # always the same generic image; see Generic Sandbox Image below

    update_status(task, "cloning")
    clone_repo_in_container(container, payload["repo"]["url"], token=payload["repo"]["token"])

    update_status(task, "running")
    # Everything from here on — analysis, installing what it needs, implementing the change,
    # verifying it, committing, pushing, opening a PR, mentioning the review bot — is the
    # agent's own responsibility. The Gateway does not orchestrate these as separate steps.
    instructions = build_agent_instructions(payload)  # see Agent Instruction Contract below
    run_agent_in_container(container, instructions)

    result = read_agent_result(container)  # parses the structured result the agent is required to emit
    stop_and_remove_container(container)

    update_status(task, "reporting")
    if result.status != "done":
        fail_task(task, error_message=result.error_message)
        send_callback(task, status="failed", error_message=task.error_message)
        return

    task.branch_name = result.branch_name
    task.programming_language = result.programming_language
    task.pr_url = result.pr_url
    task.save(update_fields=["branch_name", "programming_language", "pr_url"])

    send_callback(task, status="done", summary=result.summary, pr_url=result.pr_url)
    update_status(task, "done")
```

Keep DB writes (`update_status`) short and outside of any long-held lock or open transaction — don't wrap the whole pipeline in `transaction.atomic()`.

## Generic Sandbox Image — Important

- **One generic sandbox image, not a matrix of per-language/per-version images.** The image is the same for every task regardless of what the task turns out to need.
- The generic image bundles: **Python, Node.js, and Go** runtimes, plus **git, the coding agent CLI, curl, and build-essential**. This set is fixed and shared — it is not selected or customized per task.
- **No pre-container language/version detection.** There is only one image, so there is nothing to select.
- **The agent is responsible for self-provisioning at runtime.** Whatever the task needs beyond the generic set (a specific language/runtime version, a package, a system library) is installed by the agent itself as part of doing the work — this is not a separate Gateway-orchestrated phase (see End-to-End Flow).
- **Network policy is intentionally broader than a single-language image would need**: an allow-list covering the common registries for the languages in the generic image (PyPI, npm, Go proxy, relevant apt mirrors) plus the git remote for the target provider — not a fully open internet, but wider than "only this one language's registry."
- Phase-one monorepo assumption (single primary language/target per task) still holds.

## Agent Instruction Contract — Important

This is the core piece of Gateway-authored content in this design: the instructions handed to the agent must be clear and complete enough that **any** coding agent (not just today's specific CLI) can carry out the full request from them alone, without further Gateway-side orchestration.

The instructions passed to the agent must make explicit:

1. The full, verbatim issue/thread text — no summarization or pre-parsing by the Gateway.
2. The path to the already-cloned source inside the container.
3. That the agent is responsible for the **entire** rest of the work: figuring out what languages/tools/runtime versions are needed and installing anything missing; implementing the requested change; verifying its own work; committing and pushing using the git credentials available in its environment; opening a PR via the provider's tooling **only if the issue text asks for one**; and, **only if the issue text asks for a code review**, including a mention of the configured code-review-bot handle — in the PR description if it opened a PR, or in its final result summary if it didn't (see Code Review Loop).
4. A sensible branch-naming convention to fall back on if the issue text doesn't specify a branch name (e.g. a `Jiffy/`-prefixed slug of the task's title), so behavior stays consistent across agents without the Gateway computing the name itself.
5. **The required final output contract**: the agent must emit a structured result (e.g. JSON, at a well-known location such as `/workspace/.jiffy_result.json`, or as the final line of stdout) containing at least: `status` (`"done"` | `"failed"`), `branch_name`, `pr_url` (if any), `programming_language` (best-effort, for audit purposes), `summary`, and `error_message` (if failed). This contract is what `read_agent_result()` parses — any agent plugged into this system must honor it.

Keep the instruction-building logic (`build_agent_instructions`) in one place, and keep the wording agent-agnostic — don't bake in assumptions specific to today's particular agent CLI's conventions.

## Payload & Token Handling in Redis

- TTL for `jiffy:task:{task_id}:payload` (thread history + repo access token) should be **short — on the order of a few hours**, not days. The previous 3–7 day guidance was too generous given the token is short-lived and the whole pipeline (clone → run agent → push → PR → callback) is expected to complete well within that window.
- If a task genuinely needs longer than the TTL (e.g. due to retries), that should surface as a `failed` task with a clear "payload expired" error rather than silently extending the TTL.
- The repo access token travels only in this Redis payload and as an environment variable injected into the job's Docker container at run time. It is never written to the Django DB, never baked into any image, and is discarded once the job completes or the payload expires.

## Code Review Loop — Important

- Jiffy Gateway **never performs code review itself**, and no longer even decides whether a review was requested — that's read by the agent directly from the issue text, per the Agent Instruction Contract.
- If the issue text asks for a review, the agent includes a mention of the code-review bot itself: in the PR description if it opened one, or in its final result summary if it didn't (which the Gateway then forwards as-is in the callback report — no Gateway-side appending logic needed anymore).
- Posting that mention (wherever it ends up — PR description or Issue comment via the callback report) re-triggers the same mention-detection mechanism used to invoke Jiffy in the first place — just aimed at the review bot instead. The review bot then runs its own entirely separate task/loop, which is **out of scope for this repo**.
- The exact mention tag/handle for the code-review bot (e.g. `@jiffy-reviewer`) is **not finalized yet** — for now, make it available to the agent as a single placeholder value (e.g. an env var referenced in the instruction contract), so the actual mechanism can be swapped in later without touching the rest of the pipeline.
- Because the review happens asynchronously through this separate bot/loop, there is **no synchronous "review, then patch, then open PR" step** inside a single job run. If review feedback leads to code changes, that comes back as a **new** mention of Jiffy on the same Issue thread — i.e. a brand-new `execute_task` run.

## Container Execution

- One Docker container per job, using the **generic sandbox image** (see Generic Sandbox Image above) — never built or customized at runtime.
- Run with `remove=True` (auto-cleanup), explicit `mem_limit` and `cpus`, and a network allow-list covering the registries/toolchains needed for the languages in the generic image, plus the target provider's git remote.
- `/workspace` inside the container holds the cloned repo; inject the repo token and any other needed context as environment variables at container start, never baked into the image.
- The agent has everything it needs inside the container (git, the repo token, provider CLI/tooling) to commit, push, and open a PR itself — the Gateway does not perform these as separate host-side or Gateway-authored steps.
- The container is destroyed immediately after the agent finishes and its result has been read — the token's exposure window is the lifetime of this single container run.

## Branch Naming

There is no Gateway-side branch-naming function — the agent decides the branch name itself (using one from the issue text if specified, or a sensible fallback per the Agent Instruction Contract) and reports whatever it used back in its final result. The Gateway only stores what the agent reports; it does not compute or validate the name itself.

## Callback / Reporting

- After the job finishes (success or failure), POST the result to `task.callback_url`, passing `task.callback_secret` through as an opaque value (e.g. `Authorization: Bearer <secret>`). The Gateway does not sign, hash, or transform the secret — it stores and forwards it byte-for-byte unchanged.
- Payload includes: `task_id`, `status`, `summary` (as produced by the agent — already includes the code-review bot mention if the agent determined one was needed), `pr_url` (if the agent opened one), `error_message` (if failed).
- Retry the callback call **up to 3 times** on failure — if the edge/callback endpoint is temporarily unreachable, don't lose the result. Log failures clearly; do not silently drop them. If all 3 attempts fail, the task remains in its final local status (`done`/`failed`) with the report undelivered — this is logged, not silently swallowed.
- The central server does **not** hold any chat/comment-posting credentials (no GitHub comment token, no Telegram/Slack tokens) — posting the final comment is the responsibility of whatever handles `callback_url` (the git server or an intermediary), not this codebase.

## Credentials the Central Server Does Hold

- A **short-lived, per-task repo access token**, delivered as part of the ingestion payload, scoped to what's needed for clone/push/PR creation. Held only in Redis (with the rest of the payload) and as a container env var for the duration of the job — never persisted to the Django DB, never hardcoded.
- Nothing related to chat/comment posting (see above).

## Coding Conventions

- Standard Django app structure; keep webhook ingestion, container provisioning/clone, and agent hand-off in separate apps/modules (e.g. `ingestion/`, `execution/`) rather than one monolithic app.
- Type hints on all new functions.
- No raw SQL (see Database Strategy above) — this is a hard rule, not a style preference.
- Settings must read all secrets (`X_JIFFY_TOKEN` expected values per provider, Redis URL) from environment variables — never commit secrets or default them to real-looking values in code. Per-task repo tokens and `callback.secret` come from the request payload, not from settings.
- Favor small, testable functions for each Gateway-owned step (`start_generic_sandbox_container`, `clone_repo_in_container`, `build_agent_instructions`, `run_agent_in_container`, `read_agent_result`, `send_callback`) so they can be unit-tested independently of Celery/Docker where possible (mock the Docker/agent calls in tests).

## Out of Scope for This Repo

- Edge components (GitHub Actions / GitLab CI / Gitea Actions) that detect mentions and collect thread history — separate repos.
- Telegram and Slack integrations — not in current scope.
- Multi-language (monorepo) detection and multi-image orchestration — deferred beyond phase one.
- Any UI beyond Django Admin for task inspection.