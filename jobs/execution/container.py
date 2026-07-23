"""Manages the lifecycle of a Docker container for a task."""
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
    """Build Docker network configuration with an allow-list."""
    host = _extract_git_host(repo_url)
    allowlist: list[str] = list(settings.SANDBOX_NETWORK_ALLOWLIST)
    if host and host not in allowlist:
        allowlist.append(host)

    extra_hosts = {h: h for h in allowlist if "/" not in h}

    return {
        "extra_hosts": extra_hosts,
    }


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
        yield container
        logger.info("[%d] Container %s finished (id=%s)", task_id, container.short_id, container.id[:12])
    except ContainerError:
        raise
    except Exception as e:
        logger.exception("[%d] Failed to start sandbox container", task_id)
        raise ContainerError(f"Failed to start container: {e}")
    finally:
        pass
        # if container:
        #     try:
        #         container.stop(timeout=5)
        #     except (NotFound, Exception):
        #         pass
        #     try:
        #         container.remove(force=True)
        #         logger.info("[%d] Container %s removed (id=%s)", task_id, container.short_id, container.id[:12])
        #     except (NotFound, Exception):
        #         pass


# ---------------------------------------------------------------------------
# Git clone
# ---------------------------------------------------------------------------


def _inject_token_into_url(url: str, token: str) -> str:
    """Inject a token into a git URL for authentication.

    Converts https://github.com/user/repo.git to
    https://TOKEN@github.com/user/repo.git
    """
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        authenticated = f"{parsed.scheme}://{token}@{parsed.hostname}"
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
    container: Container, repo_url: str, token: str, task_id: int = 0
) -> None:
    """Clone the repository into the container's workspace directory."""
    logger.info("[%d] Cloning %s into container %s", task_id, _redact_url(repo_url), container.short_id)

    authenticated_url = _inject_token_into_url(repo_url, token)

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


def run_agent_in_container(
    container: Container,
    instructions: str,
    task_id: int = 0,
    timeout_seconds: int = 3600,
) -> None:
    """Run the coding agent inside the container with the given instructions."""
    logger.info("[%d] Running agent in container %s (timeout=%ds)", task_id, container.short_id, timeout_seconds)

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

    exit_code, (output, err) = container.exec_run(
        cmd=["jiffy-agent"],
        demux=True,
        workdir=WORKSPACE,
    )
    if exit_code != 0:
        error_text = (err or b"").decode(errors="replace")
        raise ContainerError(
            f"Agent exited with code {exit_code}: {error_text}"
        )
    logger.info("[%d] Agent finished in container %s (exit 0)", task_id, container.short_id)
