---
name: handoff
description: Save this task to its Kiasi task file so /clear can continue it from a small context
disable-model-invocation: true
---

Run `sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh core/taskfile.py ${CLAUDE_SESSION_ID}`. It prints the task file path. Write the file there with Write, in exactly these sections:

- `# Task`, then a line `Session: ${CLAUDE_SESSION_ID}`
- `## Goal`: the task in one or two sentences.
- `## Decisions`: what was decided and why, one line each.
- `## Checklist`: `- [ ]` for open items, `- [x]` only for an item you verified, with the word "verified" and how.
- `## Files touched`: one path per line.
- `## Next step`: the one concrete action to start with.

Read the file first if it exists and keep what is still true. Then tell the developer to run `/clear`; the next session starts from this file.
