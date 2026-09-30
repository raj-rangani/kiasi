---
description: Start the Kiasi dashboard and print its URL
---

Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dashboard.py` in the background (it stays up; do not wait for it to exit). Once it prints the port it is listening on, tell the user the URL (`http://127.0.0.1:<port>/`) so they can open it. The dashboard serves the report pages and rebuilds them every 30 minutes on its own; use `/kiasi:sync` to force an immediate rebuild.
