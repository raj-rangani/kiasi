FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates procps && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code && npm cache clean --force \
 && userdel -r node 2>/dev/null; useradd -m -u 1000 -d /Users/dev -s /bin/bash dev
USER dev
ENV HOME=/Users/dev TERM=xterm-256color CLAUDE_CODE_PLUGIN_DIRS=/Users/dev/.claude/plugins/kiasi
WORKDIR /Users/dev/my-app
