## Context rules

Every turn re-sends the whole conversation, so context size times turns is the real cost. Keep both small:
- One task per session. Start a new session or `/clear` when the task changes. Kiasi keeps one task file per task under `.kiasi/checkpoints/` (goal, decisions, verified checklist, files touched, next step), updated at compaction, stop and pause and by `/kiasi:handoff`, and restores the latest at session start. At about 150k context run `/kiasi:handoff`, then `/clear`; re-verify before marking anything in a restored task file done.
- Scripts longer than twenty lines go to a file in the scratchpad once and run by path; never re-paste a script inline.
- Never poll with sleep loops. Run background commands and wait for the notification.
- Read documents and large files by section (offset and limit, sed ranges), not whole, and never twice.
- A line starting with `[kiasi kept` or `[kiasi trimmed` names a saved file with the full text; read it by path if the cut part matters.
- Pasted content is saved under Kiasi data directory's `pastes/` folder (`~/.claude/kiasi/pastes` unless `CLAUDE_PLUGIN_DATA` points elsewhere); refer to it by path after the turn it arrived in.
- Prompts over 40k chars are refused and saved under that same `pastes/` folder; resend the instruction with the path.
- Every turn has a budget of 60 steps (the tool calls of one response are one step, so parallel calls count once) or 8M re-read tokens. At the warning, about 10 steps before the pause, finish the item in progress, write the remaining work as a checklist to the path kiasi names, then ask the developer the question kiasi gives (continue here, hand to a subagent, or stop) and do what they choose; never choose for them. At the pause every call except Agent, that question and the checklist's own Write, Read and Edit is refused: bring the checklist up to date and ask it. When kiasi gives no question, end the turn or hand the checklist to one general-purpose subagent; at a pause, end with its notice, so the developer knows how to resume.
- Loop-shaped work (parity, hardening, "anything missing?") is one round per turn: batch commands, run each test suite once, end with the checklist.
- Each subagent has its own budget of 40 steps, stated in its brief and enforced like the turn budget: at the pause it writes its checklist and replies. Scope review subagents to the diff, never the whole repo.
- A command whose output you will only scan (a build, a test run, a long log, curl) goes through the `mcp__kiasi__run` tool when it is available: the full output is saved and searchable, and only a digest enters the conversation.
- To count, filter or parse something in big files without reading them, use the `mcp__kiasi__distill` tool when it is available: it runs a short python3 or node script over the paths and only what the script prints enters the conversation.
- To read a web page, use the `mcp__kiasi__fetch` tool when it is available (url, plus `find` words): the page text is saved and searchable, and only its head and the matching lines enter the conversation. WebFetch is for a summarised answer to a question.
- While those tools are registered, a raw Bash call that matches a scan-only command, or a raw WebFetch, is refused once with the sandbox call to make instead; repeating the identical call lets it through when the exact full output is needed.
- To find something in a saved output, paste, note or checkpoint, use the `mcp__kiasi__search` tool if it is available (needs `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`), or run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search.py <words>` and read only the file it names, by section, around the hit lines it reports.

## Compact instructions

Keep verbatim: the user's current task, every file path created or edited, the exact function, class and constant names introduced, and every command that failed with its error text.
Pasted content is saved under Kiasi data directory's `pastes/` folder; keep only the path and one line per paste.
Reduce every other tool output to one line. End with the concrete next steps not yet done.
