---
name: limits
description: Set up, remove or check the Kiasi status line that feeds plan limits to the dashboard
argument-hint: "[setup|remove|status]"
disable-model-invocation: true
---

Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/statusline_install.py $ARGUMENTS` (with no argument it reports the status) and tell the user what it printed. `setup` changes `statusLine` in `~/.claude/settings.json`, keeps any status line they had running before Kiasi's part, and backs the file up once; `remove` puts their previous status line back. If it failed, show the error line as printed. Suggest running `setup` again after a plugin update so the installed copy stays current.
