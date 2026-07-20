# CLAUDE.md — Jiffy Gateway

This file gives Claude Code the technical context needed to work on the **Jiffy central server** codebase. Read this before making architectural decisions, adding dependencies, or generating code.

## What This Project Is

The central server for Jiffy: it receives pre-filtered task requests (full Issue/thread history) from lightweight edge components (GitHub Actions, GitLab CI jobs, Gitea Actions), runs an LLM coding agent against the target repository in an isolated environment, commits/pushes the result, opens a Pull/Merge Request, and reports back via a callback URL provided in the original request.

The edge components (mention detection, thread collection) are **out of scope** for this codebase — they live in separate repos/workflows. This project only implements the receiving endpoint, the job pipeline, and the callback reporter.

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

Keep the relational DB for **durable metadata only** (status, audit trail, links). Heavy/ephemeral data (full thread payloads) goes to Redis, not the DB.

```python
class Task(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("cloning", "Cloning"),
        ("running", "Running"),
        ("committing", "Committing"),
        ("pushing", "Pushing"),
        ("opening_pr", "Opening PR"),
        ("reporting", "Reporting"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    provider = models.CharField(max_length=20)   # "github" | "gitlab" | "gitea"
    repo_url = models.CharField(max_length=500)
    issue_external_id = models.CharField(max_length=100)
    title = models.CharField(max_length=255, null=True, blank=True)
    branch_name = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    callback_url = models.URLField()
    callback_secret = models.CharField(max_length=128)
    pr_url = models.URLField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

The full thread/payload (all Issue comments, metadata) is stored in Redis under `jiffy:task:{task_id}:payload` with a TTL (e.g. 3–7 days), written at ingestion time and read once by the worker. It is not modeled in Django.

## Ingestion Endpoint

- Single Django view (DRF or plain `View`), one per provider or one generic endpoint with a `provider` field in the payload — pick one convention and stay consistent.
- **Auth**: verify a shared secret / HMAC signature configured per deployment (via env var), specific to each edge component. Reject unauthenticated requests with 401 before touching the DB or Redis.
- On success:
  1. Create a `Task` row (`status="queued"`).
  2. Write the full payload to Redis (`jiffy:task:{task.id}:payload`).
  3. Enqueue `execute_task.delay(task.id)` on the `execute` Celery queue.
  4. Return `202 Accepted` immediately — do not block the HTTP response on job execution.

## Celery Configuration

- Two queues:
  - `ingest`: reserved for any lightweight async pre-processing (rarely needed given edge already filters — keep this queue minimal or skip it if unused).
  - `execute`: the actual job pipeline (clone → run agent → commit → push → PR → callback).
- Given expected volume (~100–200 requests/day across all repos), keep worker concurrency low and explicit:

```bash
celery -A jiffy worker -Q execute --concurrency=3 -n execute@%h
```

- `acks_late=True` and `task_reject_on_worker_lost=True` on the execute task, so a killed worker re-queues the job instead of silently dropping it.
- Retry policy: retry only on transient errors (network timeout, git push conflict, Docker daemon hiccup) with exponential backoff, max 3 attempts. Do **not** retry on logical/agent failures (e.g. agent couldn't complete the task) — mark `failed` and report immediately via callback.
- Use a Redis-based lock (e.g. `redis.lock` with a key like `jiffy:lock:issue:{provider}:{issue_external_id}`) before enqueueing, to guard against duplicate webhook deliveries triggering the same task twice.

## Job Pipeline (inside the Celery task)

```python
@shared_task(bind=True, max_retries=3, acks_late=True)
def execute_task(self, task_id):
    task = Task.objects.get(id=task_id)
    payload = load_payload_from_redis(task_id)  # raises if missing/expired

    update_status(task, "cloning")
    repo_dir = clone_repo(task.repo_url)  # host-side temp dir, cleaned up after

    branch = task.branch_name or generate_branch_name(task.title or extract_title(payload))

    update_status(task, "running")
    result = run_agent_in_container(repo_dir, payload, branch)

    update_status(task, "committing")
    commit_changes(repo_dir, message=build_commit_message(task, result))

    update_status(task, "pushing")
    push_branch(repo_dir, branch)

    update_status(task, "opening_pr")
    pr = open_pull_request(task, branch, result.summary)
    task.pr_url = pr.url
    task.save(update_fields=["pr_url"])

    update_status(task, "reporting")
    send_callback(task, status="done", summary=result.summary, pr_url=pr.url)

    update_status(task, "done")
    cleanup(repo_dir)
```

Keep DB writes (`update_status`) short and outside of any long-held lock or open transaction — don't wrap the whole pipeline in `transaction.atomic()`.

## Container Execution

- One Docker container per job, using a **pre-built base image** (not built at runtime) that bundles git, the coding agent CLI, and common language toolchains.
- Run with `remove=True` (auto-cleanup), explicit `mem_limit` and `cpus`, and a restricted network (only git remotes / package registries needed — no open internet).
- Mount the cloned repo as a volume (`/workspace`); inject secrets (scoped git token, task context) via environment variables at run time, never baked into the image.
- Container output (logs, final diff, agent's summary) is captured and parsed by the worker after the container exits.

## Branch Naming

```python
def generate_branch_name(title: str) -> str:
    slug = slugify(title)[:50]
    if Task.objects.filter(branch_name=f"Jiffy/{slug}").exists():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    return f"Jiffy/{slug}"
```

If the user did not provide a title/branch name in the request text, the agent must extract a concise task title from the thread content before this function is called.

## Callback / Reporting

- After the job finishes (success or failure), POST the result to `task.callback_url`, signed with `task.callback_secret` (HMAC in a header, e.g. `X-Jiffy-Signature`).
- Payload includes: `task_id`, `status`, `summary`, `pr_url` (if any), `error_message` (if failed).
- Wrap the callback call with retries (a few attempts with backoff) — if the edge endpoint is temporarily unreachable, don't lose the result. Log failures clearly; do not silently drop them.
- The central server does **not** hold any chat/comment-posting credentials (no GitHub comment token, no Telegram/Slack tokens) — posting the final comment is the edge component's responsibility, not this codebase's.

## Credentials the Central Server Does Hold

- Git provider token(s) scoped to what's needed for clone/push/PR creation (per-provider, configured via env or a `Repository`/`Credential` model — not hardcoded).
- Nothing related to chat/comment posting (see above).

## Coding Conventions

- Standard Django app structure; keep webhook ingestion, job pipeline, and container execution in separate apps/modules (e.g. `ingestion/`, `jobs/`, `execution/`) rather than one monolithic app.
- Type hints on all new functions.
- No raw SQL (see Database Strategy above) — this is a hard rule, not a style preference.
- Settings must read all secrets (git tokens, callback HMAC keys, Redis URL) from environment variables — never commit secrets or default them to real-looking values in code.
- Favor small, testable functions for each pipeline step (`clone_repo`, `run_agent_in_container`, `open_pull_request`, `send_callback`) so they can be unit-tested independently of Celery/Docker where possible (mock the Docker/API calls in tests).

## Out of Scope for This Repo

- Edge components (GitHub Actions / GitLab CI / Gitea Actions) that detect mentions and collect thread history — separate repos.
- Telegram and Slack integrations — not in current scope.
- Any UI beyond Django Admin for task inspection.