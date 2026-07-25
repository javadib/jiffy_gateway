# Jiffy Gateway

Central server for the [Jiffy](https://github.com/javadib/jiffy_gateway) task automation system. Receives pre-filtered task requests (full Issue/thread history) from lightweight edge components running on GitHub Actions, GitLab CI, or Gitea Actions, provisions an isolated sandbox container, clones the target repository, hands off the entire coding task to an AI agent, and reports results via a signed callback.

## How It Works

```
Edge (GitHub/GitLab/Gitea Actions)
        │
        │  POST /api/{provider}/ingest
        ▼
┌──────────────┐    payload     ┌─────────┐   execute_task   ┌─────────────────┐
│   Ingestion  │───────────────▶│  Redis  │◀──────dispatch───│  Celery Worker  │
│   Endpoints  │                │         │                  │  (runs jobs)    │
│   (Django)   │                └─────────┘                  └────────┬────────┘
└──────────────┘                                                     │
                                                            DOCKER_HOST (tcp)
                                                                     │
                                                          ┌──────────▼──────────┐
                                                          │ Docker Socket Proxy │
                                                          │ (restricted API)    │
                                                          └──────────┬──────────┘
                                                                     │ /var/run/docker.sock (read-only)
                                                                     ▼
                                                              Docker Engine
                                                                     │
                                                          ┌──────────▼──────────┐
                                                          │  Sandbox Container  │
                                                          │  (per-task, ephemeral)│
                                                          │  ┌────────────────┐ │
                                                          │  │  AI Agent      │ │
                                                          │  │  (OpenCode)    │ │
                                                          │  └────────────────┘ │
                                                          └─────────────────────┘
```

### End-to-end sequence

1. **Receive** -- An edge component (GitHub Action, GitLab CI job, etc.) POSTs the full Issue/thread to one of three provider-specific ingestion endpoints.
2. **Deduplicate** -- A Redis lock (`jiffy:lock:issue:{provider}:{id}`) prevents duplicate webhook deliveries from creating duplicate tasks.
3. **Store** -- The Task is written to the database (`status=queued`) and the full payload (including the repo access token) is stored in Redis with a short TTL.
4. **Provision** -- A Celery worker picks up the job, builds the sandbox image if needed, and starts an ephemeral Docker container.
5. **Clone** -- The target repository is cloned into `/workspace` inside the container using the provided repo token.
6. **Agent hand-off** -- The AI agent (OpenCode) receives the raw Issue text, the working directory path, and a detailed instruction contract. From here the agent handles everything: analyzing requirements, installing dependencies, implementing changes, verifying, committing, pushing, and optionally opening a PR.
7. **Report** -- The agent emits a structured result (JSON). The Gateway captures it, updates the Task, and POSTs a signed callback to the caller.

## Prerequisites

- Python 3.12+
- Redis
- Docker (for sandbox containers)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Quick Start

```bash
# Clone the repository
git clone https://github.com/javadib/jiffy_gateway.git
cd jiffy_gateway

# Create and fill in environment configuration
cp .env.example .env
# Edit .env with your secrets (see Configuration below)

# Install dependencies
uv sync

# Run database migrations
python manage.py migrate

# Start Redis (if not using Docker)
redis-server

# Start the development server
python manage.py runserver

# In a separate terminal, start the Celery worker
celery -A config worker -Q execute --concurrency=3 -l info
```

The API is available at `http://localhost:8000/api/`. Interactive documentation (Swagger UI) is at `http://localhost:8000/api/docs/`.

## Running with Docker Compose

```bash
cp .env.example .env
# Edit .env with your secrets

docker compose up
```

This starts four services on an internal bridge network:

| Service | Description | Port |
|---------|-------------|------|
| `web` | Django application server | 8000 (exposed) |
| `celery` | Celery worker (executes sandbox jobs) | -- |
| `docker-socket-proxy` | Restricted Docker API proxy | 2375 (internal only) |
| `redis` | Message broker and payload store | 6379 (exposed) |

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in the values.

### Django

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | Yes | -- | Django secret key |
| `DEBUG` | No | `False` | Enable debug mode |
| `ALLOWED_HOSTS` | No | `127.0.0.1,localhost` | Comma-separated allowed hosts |

### Redis

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | Yes | -- | Redis URL for cache (DB 0) |
| `CELERY_BROKER_URL` | Yes | -- | Redis URL for Celery broker (DB 1) |
| `CELERY_RESULT_BACKEND` | Yes | -- | Redis URL for Celery results (DB 2) |
| `REDIS_PASSWORD` | No | -- | Redis auth password |

### Ingestion Tokens

Each provider has its own shared secret, sent via the `X_JIFFY_TOKEN` request header and verified with constant-time comparison.

| Variable | Description |
|----------|-------------|
| `GITHUB_INGEST_TOKEN` | Shared secret for GitHub ingestion endpoint |
| `GITLAB_INGEST_TOKEN` | Shared secret for GitLab ingestion endpoint |
| `GITEA_INGEST_TOKEN` | Shared secret for Gitea ingestion endpoint |

Generate secrets with:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Sandbox

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SANDBOX_IMAGE` | No | `jiffy-sandbox:1.1.0` | Docker image for sandbox containers |
| `SANDBOX_MEM_LIMIT` | No | `1g` | Memory limit per container |
| `SANDBOX_CPU_LIMIT` | No | `1` | CPU limit per container |
| `SANDBOX_NETWORK_ALLOWLIST` | No | *(see .env.example)* | Comma-separated hostnames the sandbox can reach |

### Docker

| Variable | Description |
|----------|-------------|
| `DOCKER_HOST` | Docker daemon URL. Set automatically in docker-compose; leave unset for local development to use the default socket. |

## API Endpoints

### Ingestion

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/github/ingest` | Receive task from GitHub Actions |
| POST | `/api/gitlab/ingest` | Receive task from GitLab CI |
| POST | `/api/gitea/ingest` | Receive task from Gitea Actions |

All endpoints accept the same JSON payload shape:

```json
{
  "repo": {
    "url": "https://github.com/org/repo.git",
    "token": "ghp_..."
  },
  "issue": {
    "text": "Full issue/thread content...",
    "issue_external_id": "12345"
  },
  "callback": {
    "url": "https://your-server.com/callback",
    "secret": "hmac-signing-secret"
  }
}
```

**Headers:** `X_JIFFY_TOKEN: <provider_secret>`

**Responses:**
- `202 Accepted` -- Task enqueued successfully (or duplicate delivery detected)
- `400 Bad Request` -- Missing fields or invalid payload
- `401 Unauthorized` -- Missing or invalid `X_JIFFY_TOKEN`

### Documentation

| Path | Description |
|------|-------------|
| `/api/docs/` | Swagger UI |
| `/api/redoc/` | ReDoc |
| `/api/schema/` | OpenAPI schema (JSON) |

## Architecture

### Generic Sandbox Image

All tasks run in the same generic sandbox image, regardless of language or framework. The image bundles:

- **Python** (via uv), **Node.js** (via nvm), **Go** (via gvm)
- **Git provider CLIs** -- `gh` (GitHub), `glab` (GitLab), `tea` (Gitea)
- **OpenCode** coding agent
- **Build tools** -- build-essential, curl, git, ca-certificates

The agent is responsible for installing any additional runtime versions or packages the task requires. There is no pre-container language detection or per-language image selection.

### Docker Socket Proxy

The Celery worker manages sandbox containers through a [tecnativa/docker-socket-proxy](https://github.com/tecnativa/docker-socket-proxy) instead of mounting the Docker socket directly. This limits the worker to only the Docker API endpoints it needs:

| Endpoint Group | Enabled | Purpose |
|----------------|---------|---------|
| `CONTAINERS` | Yes | Create, start, stop, remove sandbox containers |
| `IMAGES` | Yes | Check if sandbox image exists |
| `BUILD` | Yes | Build sandbox image from Dockerfile |
| `NETWORKS` | Yes | Create/inspect `jiffy-sandbox-net` bridge network |
| `EXEC` | Yes | Exec into running containers (clone, agent run) |
| `POST` | Yes | Global toggle for write operations |
| All others | No | Disabled -- not needed |

The proxy is not exposed to the host. It is only reachable from services on the `jiffy-internal` Docker bridge network.

### Security Model

- **Repo access tokens** are stored only in Redis (4-hour TTL) and as container environment variables -- never in the Django database.
- **Ingestion secrets** are per-provider, stored in environment variables, and compared with `hmac.compare_digest`.
- **Callback payloads** are signed with HMAC-SHA256 and sent in the `X-Jiffy-Signature` header.
- **Log redaction** ensures tokens and secrets never appear in log output.
- **Sandbox network isolation** restricts containers to an allow-list of package registries and git provider hosts.

### Task Lifecycle

```
queued  ──▶  provisioning  ──▶  cloning  ──▶  running  ──▶  reporting  ──▶  done
                                                              │
                                                              └──▶  failed
```

Tasks that fail at any stage are reported as `failed` with an `error_message`. Transient errors (Docker timeouts, network issues) trigger automatic retries (up to 3, with 60-second intervals). Logical failures (agent couldn't complete the task) fail immediately without retry.

## Project Structure

```
jiffy_gateway/
├── apps/
│   └── ingestion/          # Ingestion endpoints, auth, serializers, callback dispatch
│       ├── auth.py         # Token verification (constant-time comparison)
│       ├── callback.py     # Signed callback dispatch with retries
│       ├── serializers.py  # DRF payload serializers
│       ├── tasks.py        # Celery autodiscovery re-export
│       ├── urls.py         # Provider-specific ingestion routes
│       └── views.py        # GitHub/GitLab/Gitea ingestion views
├── config/
│   ├── settings/           # Django settings (base.py, test.py)
│   ├── celery.py           # Celery app config + startup recovery
│   ├── urls.py             # Root URL routing
│   └── views.py            # Meta endpoint
├── docker/
│   └── sandbox/
│       ├── Dockerfile      # Generic sandbox image (Python, Node, Go, agent CLI)
│       ├── build.sh        # Image build script
│       └── smoke-test.sh   # Sandbox image verification
├── jobs/
│   ├── models.py           # Task model
│   ├── tasks.py            # execute_task Celery task (main pipeline)
│   ├── execution/
│   │   ├── agent.py        # Agent instruction builder + result parser
│   │   ├── container.py    # Docker container lifecycle management
│   │   └── exceptions.py   # Custom exception classes
│   └── utils/
│       └── redis.py        # Redis client + payload loader
├── templates/              # Custom Swagger UI template
├── tests/
│   ├── test_auth.py        # Token verification tests
│   ├── test_views.py       # Ingestion endpoint integration tests
│   ├── test_execution.py   # Container, agent, and pipeline tests
│   └── test_callback.py    # Callback dispatch tests
├── docker-compose.yml      # Full development stack
├── Dockerfile              # Gateway production image
├── pyproject.toml          # Dependencies and project config
└── manage.py               # Django management
```

## Testing

```bash
# Run all tests
python manage.py test

# Or with pytest
python -m pytest tests/

# With coverage
python -m pytest tests/ --cov=jobs --cov=apps
```

## Development

### Branching

- `master` -- Production releases (auto-versioned via semantic-release)
- `develop` -- Integration branch for next release

### Code Quality

- Type hints on all new functions
- No raw SQL -- Django ORM only (ensures PostgreSQL portability)
- All secrets from environment variables, never committed
- Conventional commits (`feat`, `fix`, `refactor`, `perf`, `docs`, `chore`, `ci`)

### Releasing

Releases are automated via [python-semantic-release](https://python-semantic-release.readthedocs.io/). Pushing to `master` with conventional commit messages triggers version bumping, changelog generation, and Docker image publishing to GHCR.

## Contributing

1. Fork the repository
2. Create a feature branch from `develop`
3. Make your changes with tests
4. Ensure tests pass: `python manage.py test`
5. Submit a pull request to `develop`

## License

See [LICENSE](LICENSE) for details.
