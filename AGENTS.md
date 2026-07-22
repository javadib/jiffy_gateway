# CLAUDE.md — Jiffy Gateway

This file gives Claude Code the technical context needed to work on the **Jiffy central server** codebase. Read this before making architectural decisions, adding dependencies, or generating code.

## What This Project Is

The central server for Jiffy: it receives pre-filtered task requests (full Issue/thread history) from lightweight edge components (GitHub Actions, GitLab CI jobs, Gitea Actions), uses an LLM to extract structured requirements from that free-form thread text, runs a coding agent against the target repository in an isolated environment, commits/pushes the result, optionally opens a Pull/Merge Request and/or requests a code review, and reports back via a callback URL provided in the original request.

The edge components (mention detection, thread collection) are **out of scope** for this codebase — they live in separate repos/workflows. This project only implements the receiving endpoint, the job pipeline, and the callback reporter.

## End-to-End Flow (Authoritative)

This is the full sequence once a request reaches the Gateway. Every other section in this document is a detail of one of these steps.

1. **Receive** the whole request body (the Issue/thread) from the edge component / git server via the ingestion endpoint.
2. **Extract requirements via LLM**: pass the raw issue/thread text to an LLM to extract structured fields — `task_title`, `branch_base`, `new_branch_name`, `pr_request` (bool), `code_review_request` (bool), `programming_language`, and the actual task description itself (what the coding agent is being asked to do). There is **no fixed tag/keyword format** the user must follow — extraction works on natural free-form text, the way a human reading the issue would understand it.
3. **Plan**: the LLM produces an execution plan for the coding agent based on the extracted task.
4. **Provision container**: build/select a specific container based on the extracted `programming_language` (see Language Detection & Container Images).
5. **Clone** the repo into the container.
6. **Execute** the planned task inside the container.
7. **Verify changes** — the agent checks its own work (e.g. running tests/build/lint where available) before finalizing.
8. **Commit & push** — always happens once changes are verified, regardless of `pr_request` / `code_review_request`.
9. **Open PR (conditional)** — if `pr_request` is true, open the Pull/Merge Request now.
10. **Post final report** to `callback_url` — includes status, summary, branch name, PR url (if any), and, **if `code_review_request` is true, an explicit mention of the code-review bot** appended to the report content.
11. **Code review is not performed by this codebase.** Jiffy Gateway never reviews code itself. When `code_review_request` is true, the only thing this system does is make sure the final report text mentions the code-review bot's tag, so that whoever posts the report as a comment on the Issue/PR naturally re-triggers the same mention-detection mechanism used to invoke Jiffy in the first place — just aimed at the review bot instead. The review bot then picks this up as its own, entirely separate, asynchronous task/loop (out of scope for this repo). If the review surfaces feedback the user wants acted on, that comes back as a **new** mention of Jiffy on the same thread, i.e. a brand-new `execute_task` run — not a continuation of this one.

`pr_request` and `code_review_request` are **independent flags**: a task can request review without a PR, a PR without review, both, or neither (branch is simply pushed and reported). Because the review happens asynchronously through a separate bot/loop, there is no synchronous "review, then patch, then open PR" step inside a single job run — patching in response to review feedback is always a subsequent, separate task.

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
        ("extracting", "Extracting requirements"),
        ("planning", "Planning"),
        ("cloning", "Cloning"),
        ("running", "Running"),
        ("verifying", "Verifying changes"),
        ("committing", "Committing"),
        ("pushing", "Pushing"),
        ("opening_pr", "Opening PR"),
        ("reporting", "Reporting"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    provider = models.CharField(max_length=20)   # "github" | "gitlab" | "gitea"
    repo_url = models.CharField(max_length=500)
    programming_language = models.CharField(max_length=50, null=True, blank=True)  # LLM-extracted, not detected from files
    external_issue_id = models.CharField(max_length=100)
    title = models.CharField(max_length=255, null=True, blank=True)  # LLM-extracted task_title
    branch_base = models.CharField(max_length=255, null=True, blank=True)  # LLM-extracted, defaults to repo default branch if absent
    branch_name = models.CharField(max_length=255, null=True, blank=True)  # LLM-extracted new_branch_name, or generated
    pr_request = models.BooleanField(default=False)   # LLM-extracted
    code_review_request = models.BooleanField(default=False)  # LLM-extracted; only controls whether the final
                                                                # report mentions the code-review bot — Jiffy
                                                                # itself never performs the review (see End-to-End Flow)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    callback_url = models.URLField()
    callback_secret = models.CharField(max_length=128)
    pr_url = models.URLField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

The full thread/payload (all Issue comments, metadata) **and the repo access token** are stored in Redis under `jiffy:task:{task_id}:payload` with a **short TTL** (a few hours, not days — see Payload & Token Handling below), written at ingestion time and read once by the worker. Neither the payload nor the token is ever persisted to the Django DB.

## Two Endpoints — Ingestion and Callback Dispatch

The Gateway exposes exactly two HTTP surfaces relevant to a task's lifecycle:

1. **Ingestion endpoint** (inbound): receives the task request from the edge component / git server. The payload includes the repo URL, thread history, a **short-lived repo access token** (scoped to clone/push/PR only), a `callback_url`, and a `callback_secret`.
2. **Callback dispatch** (outbound): once the job finishes (success or failure), the Gateway calls `callback_url` with the signed result. The Gateway itself never posts comments back to the Issue — whatever handles `callback_url` (the git server directly, or an intermediary) is responsible for that, using its own credentials.

The Gateway never holds or stores any chat/comment-posting credential. The repo access token it receives is used strictly for `git clone` / `git push` / opening the PR, for the lifetime of a single job, and is discarded afterward (see below).

## Ingestion Endpoint — Details

- Single Django view (DRF or plain `View`), one per provider or one generic endpoint with a `provider` field in the payload — pick one convention and stay consistent.
- **Auth**: verify a shared secret / HMAC signature configured per deployment (via env var), specific to each edge component. Reject unauthenticated requests with 401 before touching the DB or Redis.
- On success:
  1. Create a `Task` row (`status="queued"`).
  2. Write the full payload (thread history + repo access token) to Redis (`jiffy:task:{task.id}:payload`).
  3. Enqueue `execute_task.delay(task.id)` on the `execute` Celery queue.
  4. Return `202 Accepted` immediately — do not block the HTTP response on job execution.

## Celery Configuration

- **Single queue: `execute`.** There is no separate `ingest` queue — the edge components already do all mention-filtering before a request ever reaches the Gateway, so a dedicated pre-processing queue would be unused complexity given the expected volume. If a genuine need for lightweight async pre-processing emerges later, introduce the queue then, not preemptively.
- Given expected volume (~100–200 requests/day across all repos), keep worker concurrency low and explicit:

```bash
celery -A jiffy worker -Q execute --concurrency=3 -n execute@%h
```

- `acks_late=True` and `task_reject_on_worker_lost=True` on the execute task, so a killed worker re-queues the job instead of silently dropping it.
- **Retry policy (task execution)**: retry only on transient errors (network timeout, git push conflict, Docker daemon hiccup), **max 3 attempts, 1-minute interval between attempts**. Do **not** retry on logical/agent failures (e.g. agent couldn't complete the task) — mark `failed` and report immediately via callback.
- **Retry policy (callback dispatch)**: if `callback_url` is unreachable, retry **up to 3 times**. If all 3 attempts fail, log the failure clearly (the task remains `failed`/unreported from the edge's perspective) — do not silently drop it. There is currently no separate admin-alerting mechanism for a fully-failed callback; this is a known gap, not a design decision to revisit only if it becomes a real operational problem.
- Use a Redis-based lock (e.g. `redis.lock` with a key like `jiffy:lock:issue:{provider}:{external_issue_id}`) before enqueueing, to guard against duplicate webhook deliveries triggering the same task twice.

## Job Pipeline (inside the Celery task)

```python
@shared_task(bind=True, max_retries=3, acks_late=True)
def execute_task(self, task_id):
    task = Task.objects.get(id=task_id)
    payload = load_payload_from_redis(task_id)  # raises if missing/expired; includes repo token + raw thread text

    update_status(task, "extracting")
    requirements = extract_requirements_with_llm(payload)  # task_title, branch_base, new_branch_name,
                                                            # pr_request, code_review_request,
                                                            # programming_language, task_description
    apply_extracted_requirements(task, requirements)  # populates + saves the fields above on Task

    if not requirements.programming_language or not has_image_for(requirements.programming_language):
        fail_task(task, error_message=f"Unsupported or undetected language: {requirements.programming_language}")
        send_callback(task, status="failed", error_message=task.error_message)
        return

    update_status(task, "planning")
    plan = build_execution_plan(requirements)

    update_status(task, "cloning")
    repo_dir = clone_repo(task.repo_url, base=task.branch_base, token=payload["repo_token"])

    branch = task.branch_name or generate_branch_name(task.title)

    update_status(task, "running")
    result = run_agent_in_container(repo_dir, plan, branch, language=task.programming_language)

    update_status(task, "verifying")
    verify_changes(repo_dir, result)  # e.g. run tests/build/lint where available

    update_status(task, "committing")
    commit_changes(repo_dir, message=build_commit_message(task, result))

    update_status(task, "pushing")
    push_branch(repo_dir, branch, token=payload["repo_token"])

    pr_url = None
    if task.pr_request:
        update_status(task, "opening_pr")
        pr = open_pull_request(task, branch, result.summary, token=payload["repo_token"])
        pr_url = pr.url
        task.pr_url = pr_url
        task.save(update_fields=["pr_url"])

    update_status(task, "reporting")
    # Jiffy never performs the review itself. If requested, the report content simply
    # mentions the code-review bot so the same mention-detection loop picks it up as a
    # brand-new, separate task — see End-to-End Flow, step 11.
    summary = result.summary
    if task.code_review_request:
        summary = append_code_review_bot_mention(summary, branch=branch, pr_url=pr_url)

    send_callback(task, status="done", summary=summary, pr_url=pr_url)

    update_status(task, "done")
    cleanup(repo_dir)  # also drops the payload/token from Redis if not already expired
```

Keep DB writes (`update_status`) short and outside of any long-held lock or open transaction — don't wrap the whole pipeline in `transaction.atomic()`.

## Language Detection & Container Images — Important

- **One small, specialized image per supported language** (e.g. a Python image, a Node.js/TypeScript image), rather than one large general-purpose image bundling every toolchain. This keeps each image lean, fast to build/pull, and easy to maintain independently.
- **Phase-one scope**: support a small, explicit set of languages (e.g. Python, Node.js/TypeScript). Do not attempt to support "every language" up front.
- **No file-based/heuristic detection.** `programming_language` is one of the fields the LLM extracts directly from the issue/thread text during the `extracting` step (see End-to-End Flow, step 2) — the user states or implies the language in their request, the same way they'd tell a human collaborator, with no required tag or fixed format. This removes an extra clone/API-inspection step from the pipeline entirely and avoids the failure modes of marker-file heuristics (e.g. a repo without `requirements.txt` yet, or a misleading root-level file).
- **Monorepo assumption**: phase one assumes a **single primary language per task**. Multi-language (monorepo) support is explicitly out of scope for now and should be revisited later if needed — do not build speculative multi-image orchestration for it yet.
- **Unsupported or unextracted language**: if the LLM extraction step doesn't yield a `programming_language`, or it's not one of the currently supported languages, the task **fails immediately** — before any container is provisioned — with a clear `error_message` (e.g. `"Unsupported or undetected language: <value>"`) and is reported as `failed` via the callback. There is **no generic/fallback image** — this is a deliberate consequence of the "specialized image per language" decision, not an oversight.
- Each language-specific image still bundles: git, the coding agent CLI, and only the toolchain relevant to that language.

## Payload & Token Handling in Redis

- TTL for `jiffy:task:{task_id}:payload` (thread history + repo access token) should be **short — on the order of a few hours**, not days. The previous 3–7 day guidance was too generous given the token is short-lived and the whole pipeline (clone → run agent → push → PR → callback) is expected to complete well within that window.
- If a task genuinely needs longer than the TTL (e.g. due to retries), that should surface as a `failed` task with a clear "payload expired" error rather than silently extending the TTL.
- The repo access token travels only in this Redis payload and as an environment variable injected into the job's Docker container at run time. It is never written to the Django DB, never baked into any image, and is discarded once the job completes or the payload expires.

## Code Review Loop — Important

- Jiffy Gateway **never performs code review itself**. `code_review_request` is a flag that only changes what goes into the final report text — it does not trigger any review logic in this codebase.
- When `code_review_request` is true, the final report (sent to `callback_url`, and from there posted as a comment by whatever handles that URL) includes an explicit **mention of the code-review bot**, along with enough context (branch name, and PR url if one was opened) for that bot to know what to look at.
- Posting that mention re-triggers the same mention-detection mechanism used to invoke Jiffy in the first place — just aimed at the review bot instead. The review bot then runs its own entirely separate task/loop, which is **out of scope for this repo**.
- The exact mention tag/handle for the code-review bot (e.g. `@jiffy-reviewer`) is **not finalized yet** — for now, implement `append_code_review_bot_mention()` using a single placeholder constant (e.g. read from an env var or a settings constant), and keep the lookup isolated in one small function so the actual mechanism (fixed env var, per-deployment config, part of the ingestion payload, etc.) can be swapped in later without touching the rest of the pipeline.
- Because the review happens asynchronously through this separate bot/loop, there is **no synchronous "review, then patch, then open PR" step** inside a single job run. If review feedback leads to code changes, that comes back as a **new** mention of Jiffy on the same Issue thread — i.e. a brand-new `execute_task` run, not a continuation of the one that produced the reviewed code.

## Container Execution

- One Docker container per job, using a **pre-built, language-specific base image** (not built at runtime; see Language Detection & Container Images above).
- Run with `remove=True` (auto-cleanup), explicit `mem_limit` and `cpus`, and a restricted network (only git remotes / package registries needed for that language — no open internet).
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
- Payload includes: `task_id`, `status`, `summary` (with the code-review bot mention appended when `code_review_request` is true), `pr_url` (if `pr_request` was true), `error_message` (if failed).
- Retry the callback call **up to 3 times** on failure — if the edge/callback endpoint is temporarily unreachable, don't lose the result. Log failures clearly; do not silently drop them. If all 3 attempts fail, the task remains in its final local status (`done`/`failed`) with the report undelivered — this is logged, not silently swallowed.
- The central server does **not** hold any chat/comment-posting credentials (no GitHub comment token, no Telegram/Slack tokens) — posting the final comment is the responsibility of whatever handles `callback_url` (the git server or an intermediary), not this codebase.

## Credentials the Central Server Does Hold

- A **short-lived, per-task repo access token**, delivered as part of the ingestion payload, scoped to what's needed for clone/push/PR creation. Held only in Redis (with the rest of the payload) and as a container env var for the duration of the job — never persisted to the Django DB, never hardcoded.
- Nothing related to chat/comment posting (see above).

## Coding Conventions

- Standard Django app structure; keep webhook ingestion, job pipeline, and container execution in separate apps/modules (e.g. `ingestion/`, `jobs/`, `execution/`) rather than one monolithic app.
- Type hints on all new functions.
- No raw SQL (see Database Strategy above) — this is a hard rule, not a style preference.
- Settings must read all secrets (HMAC keys for edge auth, callback HMAC keys, Redis URL) from environment variables — never commit secrets or default them to real-looking values in code. Per-task repo tokens come from the request payload, not from settings.
- Favor small, testable functions for each pipeline step (`extract_requirements_with_llm`, `clone_repo`, `run_agent_in_container`, `verify_changes`, `open_pull_request`, `append_code_review_bot_mention`, `send_callback`) so they can be unit-tested independently of Celery/Docker/LLM calls where possible (mock the Docker/API/LLM calls in tests).

## Out of Scope for This Repo

- Edge components (GitHub Actions / GitLab CI / Gitea Actions) that detect mentions and collect thread history — separate repos.
- Telegram and Slack integrations — not in current scope.
- Multi-language (monorepo) detection and multi-image orchestration — deferred beyond phase one.
- Any UI beyond Django Admin for task inspection.

## Commit Message
Types: feat | fix | refactor | perf | chore | docs
After completing any task, Just write a commit message (not directly commit) with conventional commit pattern in final report