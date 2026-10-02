---
name: sync
description: Rebuild the Kiasi budget and lens reports now
disable-model-invocation: true
---

Run `sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh reports/sync.py 7` and report what it wrote (the report files under the data directory) and whether it succeeded. If the dashboard is running, its pages will pick up the new reports on next load.
