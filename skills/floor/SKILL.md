---
name: floor
description: Audit the startup floor and write the script that trims it (skills, MCP servers, agents and hooks that only some projects use)
disable-model-invocation: true
---

Run `sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh reports/floor_audit.py 30` and show its report to the developer as it is printed: the floor per project, then each finding with what it costs and what the fix does.

The audit writes a fix script next to the Kiasi data (its path is the last line of the report). The script backs every file up before touching it and only moves things the audit saw unused: skills no project invoked go name-only, skills one project used are switched off globally and on in that project, user-scope MCP servers used by some projects move to those projects, global agents used by one project move there, and hooks that attach the same text on every prompt are wrapped with `scripts/once.py`. Nothing in it is run by the audit.

Do not run or edit that script yourself, and do not edit the files it names: they are Claude Code's own settings. Tell the developer to read it and run it with `bash <path>`, then start a new session and open the Startup floor panel on the dashboard's Overview to see the floor move.
