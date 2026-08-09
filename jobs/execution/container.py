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

# Docker's embedded DNS resolver, reachable from every container on the
# default bridge network.  DNS queries are allowed so the agent can resolve
# allowlisted hosts; actual connections to non-allowlisted destinations are
# still dropped by the OUTPUT rules.
DOCKER_EMBEDDED_DNS = "127.0.0.11"


# ---------------------------------------------------------------------------
# Docker client construction
# ---------------------------------------------------------------------------


# Default request/socket timeout (seconds) for quick, bounded Docker admin
# calls (create/list/inspect/stop/remove). This is unrelated to how long a
# task is allowed to run — the agent's own exec call explicitly requests an
# unbounded timeout (see run_agent_in_container), since task execution has
# no enforced maximum duration. Resource usage over time is instead bounded
# at the container level by SANDBOX_CONTAINER_TTL_HOURS (see
# _schedule_container_expiry / remove_expired_container below).
DEFAULT_DOCKER_CLIENT_TIMEOUT_SECONDS = 60


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

    The returned client's request timeout is ``DEFAULT_DOCKER_CLIENT_TIMEOUT_SECONDS``,
    which is appropriate for the quick admin calls this client is used for
    (create/list/inspect/stop/remove). The one call that legitimately runs
    for a long, unbounded time — the agent's own exec call — overrides the
    timeout on its client directly for the duration of that call; see
    ``run_agent_in_container``.
    """
    effective_timeout = DEFAULT_DOCKER_CLIENT_TIMEOUT_SECONDS

    docker_host = os.environ.get("DOCKER_HOST")
    if docker_host:
        logger.debug("Using Docker host from DOCKER_HOST: %s", docker_host)
        return docker.from_env(timeout=effective_timeout)

    # No DOCKER_HOST set — check whether the default socket is reachable.
    # This path is fine for local development but will fail in a container
    # that does not mount the host socket.
    try:
        client = docker.from_env(timeout=effective_timeout)
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
# Network restriction helpers
# ---------------------------------------------------------------------------


def _extract_git_host(repo_url: str) -> str | None:
    """Extract the hostname from a git remote URL."""
    try:
        parsed = urlparse(repo_url)
        return parsed.hostname
    except Exception:
        return None


def _effective_network_allowlist() -> list[str]:
    """Return the effective, de-duplicated allow-list for sandbox egress.

    Merges ``SANDBOX_NETWORK_ALLOWLIST`` (the defaults, or a full override)
    with ``SANDBOX_NETWORK_ALLOWLIST_EXTRA`` (appended additions for
    self-hosted git servers and per-install LLM provider endpoints).
    """
    base = list(getattr(settings, "SANDBOX_NETWORK_ALLOWLIST", []))
    extra = list(getattr(settings, "SANDBOX_NETWORK_ALLOWLIST_EXTRA", []))
    seen: set[str] = set()
    result: list[str] = []
    for raw in base + extra:
        host = raw.strip().lower()
        if host and host not in seen:
            seen.add(host)
            result.append(host)
    return result


def _build_network_restriction_script(allowlist: list[str]) -> str:
    """Build a bash script that restricts container egress via iptables.

    The script runs as root inside the container (which must be started with
    the ``NET_ADMIN`` capability).  It allows loopback traffic, replies to
    established connections, DNS queries to Docker's embedded resolver, and
    connections to the IPs of each allowlisted host.  Everything else is
    dropped via a default-deny OUTPUT policy.

    Hosts are resolved at container start so IP changes (e.g. CDN backends)
    are picked up per run.  This is intentionally lightweight — plain
    ``iptables`` available in the Debian bookworm base image, no extra infra.
    """
    lines = [
        "#!/bin/bash",
        "set -e",
        "",
        "# Flush any existing OUTPUT rules (fresh container).",
        "iptables -F OUTPUT",
        "",
        "# Allow loopback traffic.",
        "iptables -A OUTPUT -o lo -j ACCEPT",
        "",
        "# Allow replies to established connections.",
        "iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        "",
        "# Allow DNS queries to Docker's embedded resolver.",
        f"iptables -A OUTPUT -p udp --dport 53 -d {DOCKER_EMBEDDED_DNS} -j ACCEPT",
        f"iptables -A OUTPUT -p tcp --dport 53 -d {DOCKER_EMBEDDED_DNS} -j ACCEPT",
        "",
        "# Allow connections to allowlisted hosts.",
    ]
    for host in allowlist:
        safe_host = host.replace("'", "'\\''")
        lines.append(
            "for ip in $(getent ahostsv4 '{host}' 2>/dev/null | awk '{{print $1}}' | sort -u); do "
            "iptables -A OUTPUT -d \"$ip\" -j ACCEPT; done".format(host=safe_host)
        )
    lines += [
        "",
        "# Default-deny all other egress.",
        "iptables -P OUTPUT DROP",
        "iptables -A OUTPUT -j DROP",
    ]
    return "\n".join(lines) + "\n"


def _apply_network_restriction(container: Container, allowlist: list[str], task_id: int = 0) -> None:
    """Apply iptables egress restriction inside the container.

    Runs as root (the sandbox image's default user is non-root) — the
    container must be started with the ``NET_ADMIN`` capability.  Raises
    ``ContainerError`` if the rules cannot be applied so the caller fails
    closed rather than silently running an unrestricted sandbox.
    """
    script = _build_network_restriction_script(allowlist)
    exit_code, (output, err) = container.exec_run(
        cmd=["bash", "-c", script],
        user="root",
        demux=True,
    )
    if exit_code != 0:
        detail = (err or output or b"").decode(errors="replace").strip()
        raise ContainerError(
            f"Failed to apply sandbox network restriction (exit {exit_code}): {detail}"
        )
    logger.info(
        "[%d] Network restriction applied — egress limited to %d allowlisted host(s)",
        task_id,
        len(allowlist),
    )


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
# Container TTL — hard backstop on container lifetime
# ---------------------------------------------------------------------------


def _schedule_container_expiry(container_id: str, task_id: int = 0) -> None:
    """Schedule the ``expire_sandbox_container`` Celery task for this container.

    Fires ``SANDBOX_CONTAINER_TTL_HOURS`` from now, via Celery's countdown
    mechanism on the existing ``execute`` queue — no separate beat scheduler
    needed. Runs unconditionally, independent of the task's own status, so
    every sandbox container has a hard upper bound on its lifetime.

    Imported lazily to avoid a circular import: ``jobs.tasks`` imports from
    this module at load time, so importing it back at module level here
    would deadlock the import.
    """
    from jobs.tasks import expire_sandbox_container

    ttl_seconds = settings.SANDBOX_CONTAINER_TTL_HOURS * 3600
    expire_sandbox_container.apply_async(args=[container_id, task_id], countdown=ttl_seconds)
    logger.info(
        "[%d] Container %s scheduled to expire in %sh",
        task_id,
        container_id[:12],
        settings.SANDBOX_CONTAINER_TTL_HOURS,
    )


def remove_expired_container(container_id: str, task_id: int = 0) -> None:
    """Force-remove a sandbox container once it has exceeded its TTL.

    Called by the ``expire_sandbox_container`` Celery task. Goes through the
    same Docker Socket Proxy client as every other container operation — no
    direct Docker socket access. A no-op if the container was already
    cleaned up normally (the common case).
    """
    client = get_docker_client()
    try:
        container = client.containers.get(container_id)
    except NotFound:
        logger.info(
            "[%d] TTL expiry: container %s already removed — nothing to do",
            task_id,
            container_id[:12],
        )
        return

    logger.warning(
        "[%d] TTL expiry: container %s exceeded its %sh TTL — force-removing",
        task_id,
        container_id[:12],
        settings.SANDBOX_CONTAINER_TTL_HOURS,
    )
    try:
        container.stop(timeout=5)
    except (NotFound, Exception):
        pass
    try:
        container.remove(force=True)
        logger.info("[%d] TTL expiry: container %s removed", task_id, container_id[:12])
    except NotFound:
        pass


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

    When network restriction is active (the default) the container is started
    with the ``NET_ADMIN`` capability and iptables egress rules are applied
    before the job proceeds.  If the restriction cannot be applied the
    container is torn down and a ``ContainerError`` is raised — the sandbox
    never silently runs unrestricted.
    """
    client = get_docker_client()
    container = None
    try:
        effective_allowlist = _effective_network_allowlist()
        restricted = settings.SANDBOX_NETWORK_RESTRICTED

        if restricted:
            logger.info(
                "[%d] Network restriction ACTIVE — egress limited to %d allowlisted host(s): %s",
                task_id,
                len(effective_allowlist),
                ", ".join(effective_allowlist) or "(none)",
            )
        else:
            logger.info(
                "[%d] Network restriction DISABLED (JIFFY_SANDBOX_NETWORK_RESTRICTED=false) — "
                "sandbox has open network egress",
                task_id,
            )

        logger.info(
            "[%d] Starting sandbox container (image=%s, mem=%s, cpus=%s)",
            task_id,
            settings.SANDBOX_IMAGE,
            settings.SANDBOX_MEM_LIMIT,
            settings.SANDBOX_CPU_LIMIT,
        )

        # Surface the restriction config to the container so the startup
        # report (and anything inside) can log it.  No secrets here.
        container_env = dict(env_vars)
        container_env["JIFFY_SANDBOX_NETWORK_RESTRICTED"] = "true" if restricted else "false"
        container_env["JIFFY_SANDBOX_NETWORK_ALLOWLIST"] = ",".join(effective_allowlist)

        run_kwargs: Dict[str, Any] = {
            "detach": True,
            "remove": False,
            "tty": True,
            "mem_limit": settings.SANDBOX_MEM_LIMIT,
            "cpuset_cpus": str(settings.SANDBOX_CPU_LIMIT),
            "environment": container_env,
        }
        if restricted:
            run_kwargs["cap_add"] = ["NET_ADMIN"]
            # Docker only provides the 127.0.0.11 embedded DNS resolver (which
            # the restriction script allow-lists) to containers on a
            # user-defined network. On the default "bridge" network, Docker
            # instead copies the host's own resolv.conf nameservers into the
            # container — those aren't allow-listed, so once the DROP policy
            # is in place ALL DNS resolution breaks, including for
            # allow-listed hosts.
            run_kwargs["network"] = _ensure_network(client)

        container = client.containers.run(settings.SANDBOX_IMAGE, **run_kwargs)
        logger.info("[%d] Container %s started (id=%s)", task_id, container.short_id, container.id[:12])

        # Hard backstop on container lifetime, independent of task status —
        # see _schedule_container_expiry.
        _schedule_container_expiry(container.id, task_id=task_id)

        # Inject OpenCode config into the sandbox container
        _inject_opencode_config(container, task_id)

        # Apply egress restriction before handing the container to the job.
        # Fail closed: if the rules cannot be applied, raise so the job never
        # runs against an (intended to be) restricted sandbox.
        if restricted:
            _apply_network_restriction(container, effective_allowlist, task_id=task_id)

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

    # Explicitly land on `develop` rather than trusting the remote's default
    # branch (HEAD) — the agent must always start from a known branch, and
    # this also lets the agent skip re-cloning to switch branches itself.
    exit_code, (output, err) = container.exec_run(
        cmd=["git", "fetch", "origin", "develop"],
        workdir=WORKSPACE,
        demux=True,
    )
    if exit_code != 0:
        raise ContainerError(
            f"git fetch origin develop failed (exit {exit_code}): {(err or b'').decode(errors='replace')}"
        )

    exit_code, (output, err) = container.exec_run(
        cmd=["git", "checkout", "develop"],
        workdir=WORKSPACE,
        demux=True,
    )
    if exit_code != 0:
        raise ContainerError(
            f"git checkout develop failed (exit {exit_code}): {(err or b'').decode(errors='replace')}"
        )
    logger.info("[%d] Checked out develop branch in %s", task_id, WORKSPACE)


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


def _check_oom_killed(container: Container, task_id: int = 0) -> bool:
    """Return whether the container's main process was killed by an OOM event.

    Reloads the container's state through the same client the container was
    created with (``container.reload()`` calls back through
    ``container.client.api`` — the Docker Socket Proxy, never a direct
    socket) and reads Docker's own ``State.OOMKilled`` flag, which the
    kernel/Docker set authoritatively rather than us guessing from the exit
    code alone. If the state can't be read for any reason, returns False so
    the caller falls back to generic exit-code reporting instead of masking
    the original failure.
    """
    try:
        container.reload()
        return bool(container.attrs.get("State", {}).get("OOMKilled", False))
    except Exception as e:
        logger.warning(
            "[%d] Could not determine OOM status for container %s: %s",
            task_id,
            container.short_id,
            e,
        )
        return False


def run_agent_in_container(
        container: Container,
        instructions: str,
        task_id: int = 0,
) -> None:
    """Run the coding agent inside the container with the given instructions.

    There is no enforced execution time limit — the agent runs until it
    finishes or fails on its own. Resource usage over time is instead
    bounded at the container level: every sandbox container is force-removed
    ``SANDBOX_CONTAINER_TTL_HOURS`` after its creation regardless of the
    task's status inside it (see ``_schedule_container_expiry``), so a
    hung/runaway agent cannot occupy a container indefinitely.
    """
    model = _get_opencode_model(container)
    logger.info("[%d] Running agent in container %s (no execution time limit, model=%s)", task_id, container.short_id,
                model)

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
        'cd /workspace && '
        'INSTRUCTIONS=$(cat /tmp/jiffy_instructions.txt) && '
        'opencode run --auto "$INSTRUCTIONS" > /proc/1/fd/1 2>&1'
    )

    # exec_run (non-streaming) blocks on a single HTTP request for the
    # command's entire duration, so the Docker client's own request timeout
    # is what would enforce (or kill) the execution window. Set it to no
    # timeout at all so the agent is never cut off before it finishes or
    # fails on its own — there is no artificial execution deadline.
    api_client = container.client.api
    original_timeout = api_client.timeout
    api_client.timeout = None
    try:
        exit_code, (output, err) = container.exec_run(
            cmd=["bash", "-l", "-c", run_cmd],
            demux=True,
            workdir=WORKSPACE,
        )
    finally:
        api_client.timeout = original_timeout

    if exit_code != 0:
        if _check_oom_killed(container, task_id=task_id):
            raise ContainerError(
                f"Agent process was killed by an out-of-memory (OOM) event — the "
                f"sandbox container exceeded its memory limit "
                f"({settings.SANDBOX_MEM_LIMIT}) and was killed by the kernel "
                f"(exit code {exit_code})."
            )
        raise ContainerError(
            f"Agent exited with code {exit_code}"
        )
    logger.info("[%d] Agent finished in container %s (exit 0)", task_id, container.short_id)
