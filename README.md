# Jiffy Gateway

Central server for Jiffy: receives pre-filtered task requests from edge components, provisions an isolated sandbox container, clones the target repository, hands off to a coding agent, and reports results via callback.

## Architecture

```
Edge (GitHub/GitLab/Gitea Actions)
        │
        ▼
   ┌─────────┐     ┌─────────┐     ┌──────────────────────┐
   │   Web   │────▶│  Redis  │◀────│  Celery Worker       │
   │ (Django)│     │         │     │  (runs sandbox jobs)  │
   └─────────┘     └─────────┘     └──────────┬───────────┘
                                               │
                                    DOCKER_HOST │ (tcp via proxy)
                                               ▼
                                    ┌──────────────────────┐
                                    │ Docker Socket Proxy  │
                                    │ (tecnativa/...)      │
                                    └──────────┬───────────┘
                                               │ /var/run/docker.sock (read-only)
                                               ▼
                                        Docker Engine
                                               │
                                    ┌──────────▼───────────┐
                                    │  Sandbox Container   │
                                    │  (per-task, ephemeral)│
                                    └──────────────────────┘
```

## Docker Socket Proxy

The Celery worker manages sandbox containers (create, start, stop, remove, build images, exec into containers). Rather than mounting the host's Docker socket directly into the worker container — which grants host-root-equivalent access — the worker communicates with the Docker engine through a **socket proxy**.

### Why a proxy instead of a direct mount?

A direct `/var/run/docker.sock` mount inside a container is equivalent to giving that container root access to the host. The worker runs arbitrary task code and should not have that level of privilege. The proxy exposes only the specific Docker API endpoints the worker needs, nothing else.

### Enabled endpoint groups

The proxy is configured with the minimum set of Docker API endpoint groups required by the worker:

| Group       | Enabled | Reason |
|-------------|---------|--------|
| `CONTAINERS`| Yes     | Create, start, stop, remove sandbox containers |
| `IMAGES`    | Yes     | Inspect/get images (check if sandbox image exists) |
| `BUILD`     | Yes     | Build sandbox image from Dockerfile if missing |
| `NETWORKS`  | Yes     | Inspect/create the `jiffy-sandbox-net` bridge network |
| `EXEC`      | Yes     | Exec into running containers (git clone, agent run) |
| `POST`      | Yes     | Global toggle for all write operations |
| `VOLUMES`   | No      | Not needed |
| `SERVICES`  | No      | Swarm services not used |
| `TASKS`     | No      | Swarm tasks not used |
| `SYSTEM`    | No      | System-wide operations not needed |
| `PLUGINS`   | No      | Plugin management not needed |
| `SECRETS`   | No      | Swarm secrets not used |
| `CONFIGS`   | No      | Swarm configs not used |
| `NODES`     | No      | Swarm nodes not used |
| `SWARM`     | No      | Swarm mode not used |

### Network isolation

The proxy listens on port 2375 (HTTP) but is **not exposed on any host port**. It is only reachable from services on the `jiffy-internal` Docker bridge network. The Celery worker connects to it via `DOCKER_HOST=tcp://docker-socket-proxy:2375`, set automatically in `docker-compose.yml`.

### Running

```bash
docker compose up
```

The worker automatically sets `DOCKER_HOST` to point at the proxy. The `get_docker_client()` function in `jobs/execution/container.py` validates this at runtime and raises a clear error if the environment is misconfigured.

### Local development (outside containers)

When running the worker outside Docker (e.g. `celery -A config worker` locally), leave `DOCKER_HOST` unset. The Docker SDK falls back to the default local socket (`/var/run/docker.sock`), which works for development.

## Development

```bash
# Run tests
python -m pytest tests/

# Run with coverage
python -m pytest tests/ --cov=jobs --cov=apps
```
