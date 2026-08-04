FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install Node.js 20 for the WhatsApp bridge
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p joyhousebot bridges/whatsapp && touch joyhousebot/__init__.py && \
    uv pip install --system --no-cache . && \
    rm -rf joyhousebot bridges

# Copy the full source and install
COPY joyhousebot/ joyhousebot/
COPY bridges/whatsapp/ bridges/whatsapp/
RUN uv pip install --system --no-cache .

# Build the WhatsApp bridge
WORKDIR /app/bridges/whatsapp
RUN npm install && npm run build
WORKDIR /app

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
