---
name: dashboard
description: Start the Kiasi dashboard and print its URL
disable-model-invocation: true
---

Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dashboard.py --ensure`. It returns at once: it prints the URL of the dashboard already running, or starts one detached (it outlives this session) and prints its URL. Tell the user the URL (normally `http://127.0.0.1:8787/`) so they can open it. The dashboard serves the report pages and rebuilds them every 30 minutes on its own; it is also started at every session start unless `KIASI_DASHBOARD=off`. Use `/kiasi:sync` to force an immediate rebuild. If it did not come up, read the data directory's `dashboard.log`.
