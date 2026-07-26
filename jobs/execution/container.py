"""Manages the lifecycle of a Docker container for a task."""
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator
from urllib.parse import urlparse

import docker
from docker.errors import ImageNotFound, NotFound
from docker.models.containers import Container
from django.conf import settings

from .exceptions import ContainerError

logger = logging.getLogger(__name__)

WORKSPACE = "/workspace"
AGENT_RESULT_PATH = "/workspace/.jiffy_result.json"

# Path to the sandbox Dockerfile, relative to the project root.
SANDBOX_DOCKERFILE_DIR = Path(__file__).resolve().parent.parent.parent / "docker" / "sandbox"


# ---------------------------------------------------------------------------
# Docker client construction
# ---------------------------------------------------------------------------


def get_docker_client() -> docker.DockerClient:
    """Return a Docker client configured from the DOCKER_HOST environment variable.

    In containerised deployments the Celery worker connects to the Docker
    engine through a socket proxy (``docker-socket-proxy``), so ``DOCKER_HOST``
    **must** be set (e.g. ``tcp://docker-socket-proxy:2375``).  If it is
    missing the worker would silently fall back to a host socket path that
    does not exist inside its own container, producing confusing connection
    errors later.  This function checks up front and raises a clear error.

    Outside containers (local development) the environment variable is
    optional — the Docker SDK falls back to the default socket automatically.
    """
    docker_host = os.environ.get("DOCKER_HOST")
    if docker_host:
        logger.debug("Using Docker host from DOCKER_HOST: %s", docker_host)
        return docker.from_env()

    # No DOCKER_HOST set — check whether the default socket is reachable.
    # This path is fine for local development but will fail in a container
    # that does not mount the host socket.
    try:
        client = docker.from_env()
        # Lightweight connectivity check
        client.ping()
        return client
    except Exception as exc:
        raise ContainerError(
            "DOCKER_HOST environment variable is not set and the default "
            "Docker socket is not reachable.  In containerised deployments, "
            "set DOCKER_HOST to the socket proxy address "
            "(e.g. tcp://docker-socket-proxy:2375).  Original error: " + str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# Sandbox image management
# ---------------------------------------------------------------------------


def ensure_sandbox_image() -> None:
    """Ensure the configured SANDBOX_IMAGE exists locally.

    If the image is already present this is a cheap no-op.  If it is missing,
    builds it from ``docker/sandbox/Dockerfile`` and tags it.  Logs which path
    was taken and how long it took so operators can see it in the console.

    This function is called both at worker startup (via the ``worker_ready``
    signal) and before each job's container is provisioned, as a safety net
    in case the image was removed between startup and job execution.

    Raises ``ContainerError`` if the build fails.
    """
    image_ref = settings.SANDBOX_IMAGE
    client = get_docker_client()

    t0 = time.monotonic()
    try:
        client.images.get(image_ref)
        elapsed = time.monotonic() - t0
        logger.info(
            "Sandbox image %s found locally — no build needed (%.1fs)",
            image_ref,
            elapsed,
        )
        return
    except ImageNotFound:
        pass

    logger.info(
        "Sandbox image %s not found locally, building from %s",
        image_ref,
        SANDBOX_DOCKERFILE_DIR,
    )
    try:
        client.images.build(
            path=str(SANDBOX_DOCKERFILE_DIR),
            tag=image_ref,
            rm=True,
        )
        elapsed = time.monotonic() - t0
        logger.info(
            "Sandbox image %s built successfully in %.1fs",
            image_ref,
            elapsed,
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        msg = f"Failed to build sandbox image {image_ref} after {elapsed:.1f}s: {exc}"
        logger.error(msg)
        raise ContainerError(msg) from exc


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------


def _extract_git_host(repo_url: str) -> str | None:
    """Extract the hostname from a git remote URL."""
    try:
        parsed = urlparse(repo_url)
        return parsed.hostname
    except Exception:
        return None


def _build_network_config(repo_url: str) -> dict:
    """Build Docker network configuration for the sandbox container."""
    return {}


def _ensure_network(client: docker.DockerClient) -> str:
    """Ensure the jiffy-sandbox bridge network exists; return its name."""
    network_name = "jiffy-sandbox-net"
    try:
        client.networks.get(network_name)
    except NotFound:
        client.networks.create(
            network_name,
            driver="bridge",
            internal=False,
        )
        logger.info("Created Docker network %s", network_name)
    return network_name


# ---------------------------------------------------------------------------
# Startup self-report
# ---------------------------------------------------------------------------

_STARTUP_REPORT_SCRIPT = """\
echo ''
echo '==========================================='
echo '   Jiffy Sandbox Container - Startup Report'
echo '==========================================='
echo ''
echo '-- Runtime Versions --'
echo -n '  Python  : '; python3 --version 2>&1 || echo 'not found'
echo -n '  Node.js : '; node --version 2>&1 || echo 'not found'
echo -n '  npm     : '; npm --version 2>&1 || echo 'not found'
echo -n '  Go      : '; go version 2>&1 || echo 'not found'
echo ''
echo '-- Tools --'
echo -n '  git     : '; git --version 2>&1 || echo 'not found'
echo -n '  curl    : '; curl --version 2>&1 | head -1 || echo 'not found'
echo -n '  gcc     : '; gcc --version 2>&1 | head -1 || echo 'not found'
echo -n '  make    : '; make --version 2>&1 | head -1 || echo 'not found'
echo -n '  uv      : '; uv --version 2>&1 || echo 'not found'
echo -n '  gh      : '; gh --version 2>&1 | head -1 || echo 'not found'
echo ''
echo '-- Coding Agent --'
echo -n '  opencode: '; opencode --version 2>&1 || echo 'not found'
echo -n '  model   : '
python3 -c "
import sys, json
try:
    c = json.load(open('/home/jiffy/.config/opencode/opencode.json'))
    m = c.get('model') or ''
    if not m and isinstance(c.get('provider'), dict):
        m = c['provider'].get('model', '')
    print(m or 'unknown')
except Exception:
    print('unknown')
" 2>&1
echo ''
echo '-- Environment --'
echo -n '  user    : '; whoami 2>&1 || echo 'unknown'
echo -n '  workdir : '; ls -la /workspace 2>&1 | head -1 || echo '/workspace not present'
echo -n '  hostname: '; hostname 2>&1 || echo 'unknown'
echo ''
echo '-- Network --'
(echo -n '  pypi.org : '; curl -sI --max-time 3 https://pypi.org 2>&1 | head -1 || echo 'unreachable')
(echo -n '  npmjs.org: '; curl -sI --max-time 3 https://registry.npmjs.org 2>&1 | head -1 || echo 'unreachable')
(echo -n '  github.com: '; curl -sI --max-time 3 https://github.com 2>&1 | head -1 || echo 'unreachable')
echo '==========================================='
echo ''
"""


def log_sandbox_startup(container: Container, task_id: int = 0) -> None:
    """Run the startup self-report inside the container.

    Prints a structured summary of installed tools/runtimes, the coding agent
    model, and environment info to the container's stdout (captured by Docker's
    logging driver) and to the host-side logger.
    """
    model = _get_opencode_model(container)
    logger.info("[%d] Running sandbox startup self-report (model=%s)", task_id, model)

    exit_code, (output, _) = container.exec_run(
        cmd=["bash", "-l", "-c", _STARTUP_REPORT_SCRIPT],
        demux=True,
    )

    report = (output or b"").decode(errors="replace")

    if exit_code != 0:
        logger.warning("[%d] Startup report script exited with code %d:\n%s", task_id, exit_code, report.strip())

    logger.info("[%d] Startup self-report generated", task_id)

    # Write the captured output to the container's main stdout so docker logs
    # captures the full report.
    if report.strip():
        escaped = report.replace("\\", "\\\\").replace("'", "'\\''")
        container.exec_run(
            cmd=["bash", "-c", "printf '%s' '" + escaped + "' > /proc/1/fd/1"],
            demux=True,
        )


# ---------------------------------------------------------------------------
# Config injection
# ---------------------------------------------------------------------------

SANDBOX_OPENCODE_CONFIG_PATH_IN_CONTAINER = "/home/jiffy/.config/opencode/opencode.json"


def _inject_opencode_config(container: Container, task_id: int) -> None:
    """Read the OpenCode config file and write it into the sandbox container.

    This avoids volume-mount path issues when the Celery worker runs inside
    Docker (the Docker daemon needs host-accessible paths, but the config
    file is only accessible inside the Celery container).
    """
    # Read opencode.json from the project root
    config_path = Path(__file__).resolve().parent.parent.parent / "opencode.json"
    if not config_path.is_file():
        logger.warning(
            "[%d] opencode.json not found in project root: %s",
            task_id,
            config_path,
        )
        return

    try:
        config_content = config_path.read_text(encoding="utf-8")
        # Write config into the container using printf + heredoc to avoid escaping issues
        escaped = config_content.replace("\\", "\\\\").replace("'", "'\\''")
        write_cmd = f"printf '%s' '{escaped}' > {SANDBOX_OPENCODE_CONFIG_PATH_IN_CONTAINER}"

        logger.info(f"[%d] OpenCode config: %s", task_id, write_cmd)

        exit_code, (_, err) = container.exec_run(
            cmd=["bash", "-c", write_cmd],
            demux=True,
        )
        if exit_code != 0:
            logger.warning(
                "[%d] Failed to inject OpenCode config: %s",
                task_id,
                (err or b"").decode(errors="replace"),
            )
        else:
            logger.info("[%d] Injected OpenCode config into sandbox container", task_id)
    except Exception as e:
        logger.warning("[%d] Failed to inject OpenCode config: %s", task_id, e)


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------


@contextmanager
def start_generic_sandbox_container(
        task_id: int,
        env_vars: Dict[str, Any],
        repo_url: str = "",
) -> Generator[Container, None, None]:
    """Start a generic sandbox container for the given task.

    Manages the full lifecycle: start, yield for use, stop, and remove.
    On any error the container is always cleaned up.
    """
    client = get_docker_client()
    container = None
    try:
        logger.info(
            "[%d] Starting sandbox container (image=%s, mem=%s, cpus=%s)",
            task_id,
            settings.SANDBOX_IMAGE,
            settings.SANDBOX_MEM_LIMIT,
            settings.SANDBOX_CPU_LIMIT,
        )
        _ensure_network(client)

        networking_config = {}
        if repo_url:
            networking_config = _build_network_config(repo_url)

        container = client.containers.run(
            settings.SANDBOX_IMAGE,
            detach=True,
            remove=False,
            tty=True,
            mem_limit=settings.SANDBOX_MEM_LIMIT,
            cpuset_cpus=str(settings.SANDBOX_CPU_LIMIT),
            environment=env_vars,
            network="jiffy-sandbox-net",
            **networking_config,
        )
        logger.info("[%d] Container %s started (id=%s)", task_id, container.short_id, container.id[:12])

        # Inject OpenCode config into the sandbox container
        _inject_opencode_config(container, task_id)

        yield container
        logger.info("[%d] Container %s finished (id=%s)", task_id, container.short_id, container.id[:12])
    except ContainerError:
        raise
    except Exception as e:
        logger.exception("[%d] Failed to start sandbox container", task_id)
        raise ContainerError(f"Failed to start container: {e}")
    finally:
        if container:
            if settings.SANDBOX_CLEANUP:
                try:
                    container.stop(timeout=5)
                except (NotFound, Exception):
                    pass
                try:
                    container.remove(force=True)
                    logger.info("[%d] Container %s removed (id=%s)", task_id, container.short_id, container.id[:12])
                except (NotFound, Exception):
                    pass
            else:
                logger.info(
                    "[%d] Container %s cleanup skipped (JIFFY_SANDBOX_CLEANUP=false)",
                    task_id,
                    container.short_id,
                )


# ---------------------------------------------------------------------------
# Git clone
# ---------------------------------------------------------------------------


def _inject_token_into_url(url: str, token: str, provider: str = "github", username: str = "") -> str:
    """Inject a token into a git URL for authentication.

    Provider-specific formats:
    - GitHub:  https://TOKEN@github.com/user/repo.git
    - GitLab:  https://USERNAME:TOKEN@gitlab.example.com/user/repo.git
    - Gitea:   https://USERNAME:TOKEN@gitea.example.com/user/repo.git
    """
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        # GitLab and Gitea require username:token format
        if provider in ("gitlab", "gitea") and username:
            userinfo = f"{username}:{token}"
        else:
            userinfo = token
        authenticated = f"{parsed.scheme}://{userinfo}@{parsed.hostname}"
        if parsed.port:
            authenticated += f":{parsed.port}"
        authenticated += parsed.path
        if parsed.query:
            authenticated += f"?{parsed.query}"
        return authenticated
    return url


def _redact_url(url: str) -> str:
    """Redact any token/userinfo from a URL for safe logging."""
    parsed = urlparse(url)
    if parsed.username:
        redacted_netloc = parsed.netloc.replace(parsed.username, "***")
        return url.replace(parsed.netloc, redacted_netloc)
    return url


def clone_repo_in_container(
        container: Container,
        repo_url: str,
        token: str,
        task_id: int = 0,
        provider: str = "github",
        username: str = "",
) -> None:
    """Clone the repository into the container's workspace directory."""
    logger.info("[%d] Cloning %s into container %s", task_id, _redact_url(repo_url), container.short_id)

    authenticated_url = _inject_token_into_url(repo_url, token, provider=provider, username=username)

    exit_code, (output, err) = container.exec_run(
        cmd=["git", "clone", authenticated_url, WORKSPACE],
        demux=True,
    )
    if exit_code != 0:
        raise ContainerError(
            f"git clone failed (exit {exit_code}): {(err or b'').decode(errors='replace')}"
        )
    logger.info("[%d] Repository cloned into %s", task_id, WORKSPACE)


# ---------------------------------------------------------------------------
# Agent execution
# ---------------------------------------------------------------------------


def _get_opencode_model(container: Container) -> str:
    """Read the configured LLM model from opencode's config inside the container.

    Returns the model name or "unknown" if not found.
    """
    try:
        exit_code, (output, _) = container.exec_run(
            cmd=["cat", SANDBOX_OPENCODE_CONFIG_PATH_IN_CONTAINER], demux=True
        )
        if exit_code == 0 and output:
            config = json.loads(output)
            # opencode config has provider.model format like "anthropic/claude-sonnet-4-20250514"
            model = config.get("model")
            if model:
                return model
            # Check nested provider config
            provider = config.get("provider", {})
            if isinstance(provider, dict):
                model = provider.get("model")
                if model:
                    return model
    except (json.JSONDecodeError, Exception):
        pass
    return "unknown"


def run_agent_in_container(
        container: Container,
        instructions: str,
        task_id: int = 0,
        timeout_seconds: int = 3600,
) -> None:
    """Run the coding agent inside the container with the given instructions."""
    model = _get_opencode_model(container)
    logger.info("[%d] Running agent in container %s (timeout=%ds, model=%s)", task_id, container.short_id,
                timeout_seconds, model)

    escaped_instructions = instructions.replace("\\", "\\\\").replace("'", "'\\''")
    write_cmd = f"printf '%s' '{escaped_instructions}' > /tmp/jiffy_instructions.txt"
    exit_code, (output, err) = container.exec_run(
        cmd=["bash", "-c", write_cmd],
        demux=True,
    )
    if exit_code != 0:
        raise ContainerError(
            f"Failed to write instructions file: {(err or b'').decode(errors='replace')}"
        )

    # Read instructions from file and pass to opencode
    # Use login shell (-l) so .profile is sourced and all tools (nvm, uv, etc.) are available
    # Redirect agent stdout/stderr to the container's main stdout so docker logs
    # shows real-time output; exit code remains captured via exec_run return value.
    run_cmd = (
        'INSTRUCTIONS=$(cat /tmp/jiffy_instructions.txt) && '
        'opencode run --auto "$INSTRUCTIONS" > /proc/1/fd/1 2>&1'
    )
    exit_code, (output, err) = container.exec_run(
        cmd=["bash", "-l", "-c", run_cmd],
        demux=True,
        workdir=WORKSPACE,
    )
    if exit_code != 0:
        raise ContainerError(
            f"Agent exited with code {exit_code}"
        )
    logger.info("[%d] Agent finished in container %s (exit 0)", task_id, container.short_id)
