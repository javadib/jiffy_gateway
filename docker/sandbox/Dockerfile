# Jiffy Generic Sandbox Image
#
# One generic image for all tasks — includes Python, Node.js, Go, git, curl,
# build-essential, and the coding agent CLI. The agent is responsible for
# installing any additional dependencies at runtime.
#
# Build: docker build -t jiffy-sandbox:1.0.0 .
# The image tag used at runtime is read from SANDBOX_IMAGE env var.

FROM debian:bookworm-20260713

ARG DEBIAN_FRONTEND=noninteractive

# 1. Base dependencies + language runtimes
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    ca-certificates \
    gnupg \
    python3 \
    python3-pip \
    python3-venv \
    && mkdir -p /etc/apt/keyrings \
    && rm -rf /var/lib/apt/lists/*

# 2. Install the coding agent CLI
#    In production this would install the real CLI binary. For now, a
#    placeholder that writes a result file so the pipeline can be tested end-to-end.
RUN printf '#!/bin/sh\n\
set -e\n\
INSTRUCTIONS_PATH="${JIFFY_INSTRUCTIONS_PATH:-/tmp/jiffy_instructions.txt}"\n\
if [ ! -f "$INSTRUCTIONS_PATH" ]; then\n\
  echo "No instructions file at $INSTRUCTIONS_PATH" >&2\n\
  exit 1\n\
fi\n\
echo "Coding agent: instructions received at $INSTRUCTIONS_PATH — agent would process them here." >&2\n\
exit 0\n' > /usr/local/bin/coding-agent-cli \
    && chmod +x /usr/local/bin/coding-agent-cli

# 3. Non-root user
RUN useradd -ms /bin/bash jiffy \
    && mkdir -p /workspace && chown jiffy:jiffy /workspace

USER jiffy
WORKDIR /workspace

# Keep the container alive so exec_run can issue commands into it.
# bash -c 'while true; do sleep 1d; done' is more portable than tail -f /dev/null
# and avoids issues on minimal images that lack tail.
CMD ["bash", "-c", "while true; do sleep 86400; done"]
