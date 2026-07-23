# Jiffy Generic Sandbox Image
#
# One generic image for all tasks — includes version managers (nvm, uv, gvm)
# for Node.js/Python/Go, git provider CLIs (gh, glab, tea), git, curl,
# build-essential, and the OpenCode coding agent CLI.
#
# The agent is responsible for installing specific language/runtime versions
# at runtime via the version managers, and for any additional dependencies.
#
# Build: docker build -t jiffy-sandbox:1.1.0 .
# The image tag used at runtime is read from SANDBOX_IMAGE env var.

FROM debian:bookworm-20260713

ARG DEBIAN_FRONTEND=noninteractive

# Pinned tool versions
ARG NVM_VERSION=0.40.3
ARG GH_VERSION=2.72.0
ARG GLAB_VERSION=1.52.0
ARG TEA_VERSION=0.9.0

# 1. Base dependencies (kept from original image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    ca-certificates \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && rm -rf /var/lib/apt/lists/*

# 2. Version managers — Node.js (nvm), Python (uv), Go (gvm)
#    The agent installs specific language versions at runtime via these managers.

# --- nvm (Node Version Manager) ---
ENV NVM_DIR=/usr/local/nvm
RUN mkdir -p "$NVM_DIR" \
    && curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh | bash

# Make nvm available in non-interactive shells via /etc/profile.d
RUN printf 'export NVM_DIR="%s"\n[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"\n' "$NVM_DIR" \
    > /etc/profile.d/nvm.sh

# --- uv (Python package/version manager) ---
# Installed to /usr/local/bin — available to all users without sourcing.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# --- gvm (Go Version Manager) ---
ENV GVM_DIR=/usr/local/gvm
RUN mkdir -p "$GVM_DIR" \
    && git clone https://github.com/moovweb/gvm.git "$GVM_DIR" \
    && chmod +x "$GVM_DIR/bin/gvm"

# Make gvm available in non-interactive shells via /etc/profile.d
RUN printf 'export GVM_DIR="%s"\n[ -s "$GVM_DIR/scripts/gvm" ] && . "$GVM_DIR/scripts/gvm"\n' "$GVM_DIR" \
    > /etc/profile.d/gvm.sh

# Allow jiffy user to write GVM version directories and symlinks
RUN chown -R root:jiffy "$GVM_DIR" && chmod -R g+w "$GVM_DIR"

# 3. Git provider CLIs — pinned versions

# --- gh (GitHub CLI) ---
RUN curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
    | tar xz --strip-components=2 -C /usr/local/bin "gh_${GH_VERSION}_linux_amd64/bin/gh"

# --- glab (GitLab CLI) ---
RUN curl -fsSL "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/glab_${GLAB_VERSION}_linux_amd64.tar.gz" \
    | tar xz --strip-components=1 -C /usr/local/bin "glab_${GLAB_VERSION}_linux_amd64/bin/glab"

# --- tea (Gitea CLI) ---
RUN curl -fsSL "https://gitea.com/gitea/tea/releases/download/v${TEA_VERSION}/tea_${TEA_VERSION}_linux_amd64.tar.gz" \
    | tar xz -C /usr/local/bin "tea"

# 4. Coding agent CLI: OpenCode
#    Installed via official install script, pinned to specific version.
#    Runs in non-interactive mode from /workspace.
#    OpenCode auto-discovers AGENTS.md (or falls back to CLAUDE.md) from CWD.
#    OPENCODE_DISABLE_CLAUDE_CODE is intentionally NOT set so the fallback works.
RUN curl -fsSL https://opencode.ai/install | bash

# 5. Agent wrapper — sets up environment and invokes OpenCode non-interactively
RUN printf '#!/bin/bash\n\
# Source version managers for non-interactive shell\n\
[ -s /etc/profile.d/nvm.sh ] && . /etc/profile.d/nvm.sh\n\
[ -s /etc/profile.d/gvm.sh ] && . /etc/profile.d/gvm.sh\n\
\n\
INSTRUCTIONS_PATH="${JIFFY_INSTRUCTIONS_PATH:-/tmp/jiffy_instructions.txt}"\n\
if [ ! -f "$INSTRUCTIONS_PATH" ]; then\n\
  echo "No instructions file at $INSTRUCTIONS_PATH" >&2\n\
  exit 1\n\
fi\n\
\n\
INSTRUCTIONS=$(cat "$INSTRUCTIONS_PATH")\n\
opencode run --auto "$INSTRUCTIONS"\n\
' > /usr/local/bin/jiffy-agent \
    && chmod +x /usr/local/bin/jiffy-agent

# 6. Non-root user
RUN useradd -ms /bin/bash jiffy \
    && mkdir -p /workspace && chown jiffy:jiffy /workspace

USER jiffy
WORKDIR /workspace

# Keep the container alive so exec_run can issue commands into it.
# bash -c 'while true; do sleep 1d; done' is more portable than tail -f /dev/null
# and avoids issues on minimal images that lack tail.
CMD ["bash", "-c", "while true; do sleep 86400; done"]
