FROM python:3.12-slim

# Universal tools + Node 20 (Claude Code is an npm package) + GitHub CLI.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git ripgrep fd-find jq \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && ln -s "$(command -v fdfind)" /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*

# uv (installs + runs the memory MCP server).
RUN pip install --no-cache-dir uv

# mise: polyglot version manager (single static binary, arm64, non-root).
RUN curl -fsSL https://mise.run | MISE_INSTALL_PATH=/usr/local/bin/mise sh \
 && chmod +x /usr/local/bin/mise \
 && mise --version \
 # Keep mise shims on PATH in login shells too (interactive `bash -l` re-sources
 # /etc/profile and would otherwise drop the ENV PATH, shadowing pinned runtimes
 # with system Node).
 && printf 'export PATH=/opt/mise/data/shims:$PATH\n' > /etc/profile.d/mise.sh

# Claude Code CLI (the bundled reference MCP client).
RUN npm install -g @anthropic-ai/claude-code

# Memory MCP server: EDITABLE install so db.py resolves ../sql/001_schema.sql
# via Path(__file__).resolve().parents[2] / "sql".
COPY memory-service/pyproject.toml /opt/memory/pyproject.toml
COPY memory-service/uv.lock        /opt/memory/uv.lock
COPY memory-service/src            /opt/memory/src
COPY memory-service/sql            /opt/memory/sql
RUN cd /opt/memory && uv sync

# Baked role config, skills, entrypoint, and the toolset bootstrap script.
COPY agent-worker/config        /opt/agent-worker/config
COPY agent-worker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY agent-worker/bootstrap.sh  /usr/local/bin/bootstrap.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/bootstrap.sh

# Non-root user + mise data dir (volume-backed at runtime; owned by agent).
RUN useradd --create-home --shell /bin/bash agent \
 && mkdir -p /opt/mise/data \
 && chown -R agent:agent /opt/mise/data
USER agent
WORKDIR /workspace

ENV CONFIG_DIR=/opt/agent-worker/config \
    MEMORY_PYTHON=/opt/memory/.venv/bin/python \
    MISE_DATA_DIR=/opt/mise/data \
    MISE_TRUSTED_CONFIG_PATHS=/workspace \
    PATH=/opt/mise/data/shims:$PATH

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
