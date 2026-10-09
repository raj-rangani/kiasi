#!/bin/bash
# Starts Claude Code in the Mac-like demo container; see docs/development.md. The login file is mounted from the host, never copied.
E=$(dirname "$(readlink -f "$0")")
HOME_DIR=${DEMO_HOME:-$E/home}                        # holds .claude/ (settings.json, kiasi/statusline.py, plugins/data/) and .claude.json
PROJECT=${DEMO_PROJECT:-$E/my-app}                    # a folder with a package.json named my-app and a git repo on main
MODULES=${DEMO_NODE_MODULES:-$PROJECT/node_modules}   # the node_modules to list, mounted read-only
KIASI=${DEMO_KIASI:-$(cd "$E/../../.." && pwd)}       # the kiasi checkout, mounted read-only as the plugin
exec docker run --rm ${DEMO_TTY:--it} \
  -v "$HOME_DIR/.claude:/Users/dev/.claude" \
  -v "$HOME_DIR/.claude.json:/Users/dev/.claude.json" \
  -v "$HOME/.claude/.credentials.json:/Users/dev/.claude/.credentials.json" \
  -v "$KIASI:/Users/dev/.claude/plugins/kiasi:ro" \
  -v "$PROJECT:/Users/dev/my-app" \
  -v "$MODULES:/Users/dev/my-app/node_modules:ro" \
  kiasi-demo "$@"
