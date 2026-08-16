FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p porthouse evals/suites && touch porthouse/__init__.py && \
    uv pip install --system --no-cache '.[observability]' && \
    rm -rf porthouse

# Copy the full source and install
COPY porthouse/ porthouse/
COPY evals/suites/ evals/suites/
RUN uv pip install --system --no-cache '.[observability]'

# Compose an explicit runtime image from independently installable extensions.
# The default Docker image remains Core-only when the build arg is empty.
ARG PORTHOUSE_EXTENSIONS=""
COPY extensions/ extensions/
RUN set -eu; \
    for extension_id in ${PORTHOUSE_EXTENSIONS}; do \
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
RUN useradd --create-home --uid 1000 porthouse && \
    mkdir -p /home/porthouse/.porthouse && \
    chown -R porthouse:porthouse /app /home/porthouse/.porthouse
USER porthouse

# Cloud API default port
EXPOSE 18790

ENTRYPOINT ["porthouse"]
CMD ["api"]
