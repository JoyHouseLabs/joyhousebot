FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY pyproject.toml README.md LICENSE ./
# uv must see every declared workspace member before it can resolve the root
# project. The Runtime depends on package-protocol; copying all small SDK
# workspaces here also keeps the workspace graph valid in this cache layer.
COPY packages/package-protocol/ packages/package-protocol/
COPY packages/extension-sdk-python/ packages/extension-sdk-python/
COPY sdks/python/ sdks/python/
RUN mkdir -p joyhousebot evals/suites && touch joyhousebot/__init__.py && \
    uv pip install --system --no-cache '.[observability]' && \
    rm -rf joyhousebot

# Copy the full source and install
COPY joyhousebot/ joyhousebot/
COPY evals/suites/ evals/suites/
RUN uv pip install --system --no-cache '.[observability]'

# Compose an explicit runtime image from independently installable extensions.
# The default Docker image remains Core-only when the build arg is empty.
ARG JOYHOUSEBOT_EXTENSIONS=""
COPY extensions/ extensions/
RUN set -eu; \
    for extension_id in ${JOYHOUSEBOT_EXTENSIONS}; do \
      case "${extension_id}" in *[!a-z0-9-]*|'') exit 2;; esac; \
      test -f "extensions/${extension_id}/pyproject.toml"; \
      uv pip install --system --no-cache "./extensions/${extension_id}"; \
    done; \
    rm -rf extensions

# Run as a non-root user by default (api/scheduler/channel-worker roles).
# NOTE: the agent worker role executes commands in Docker sandbox containers
# and therefore needs access to /var/run/docker.sock. When running the worker
# role with the socket mounted, either run that container as root
# (`docker run --user root ...` / compose `user: root`) or grant the
# container the host docker group via compose `group_add`. The default CMD
# (api) does not need the socket and stays unprivileged.
RUN useradd --create-home --uid 1000 joyhousebot && \
    mkdir -p /home/joyhousebot/.joyhousebot && \
    chown -R joyhousebot:joyhousebot /app /home/joyhousebot/.joyhousebot
USER joyhousebot

# Cloud API default port
EXPOSE 18790

ENTRYPOINT ["joyhousebot"]
CMD ["api"]
