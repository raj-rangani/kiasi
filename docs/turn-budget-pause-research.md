# Turn budget: from a silent stop to a visible, resumable pause

Research date: 2026-10-06. Claude Code on this machine: 2.1.291.
Problem: when Kiasi's turn budget fires, developers see Claude go quiet mid-task, think the chat died,
redo the work, and some uninstall Kiasi.

## 1. What actually happens today (checked in code and in this session's Kiasi log)

1. **The stop looks like a crash.** `turn_guard` (`scripts/core/turn.py:62`) returns a PostToolUse
   `{"decision": "block", "reason": ...}`. In Claude Code that only adds text next to the tool result for
   Claude, so it stops nothing. The terminal shows it as `PostToolUse:<Tool> hook blocking error from
   command: …` (claude-code #31301). There is no `systemMessage`, so no plain-language notice reaches the
   developer. The desktop app may not show the reason at all (#74299).
2. **Subagent tool calls count against the parent turn.** The counter key is
   `transcript_key(transcript_path)` (`scripts/core/transcript.py:141`), with no `agent_id`. In this session
   Kiasi logged `turn_warn` at 10:08:38 with `steps: 30` under the main transcript, while the main agent had
   made only 7 calls. The other 23 came from research subagents. So fanning work out to subagents trips the
   parent's warning and stop, and the stop text goes to whichever agent makes the call that crosses the
   line. `scripts/core/reads.py:39` already keys by `agent_id`; `turn.py` does not.
3. **Counts are lost under parallel load.** The same turn's state recorded `steps: 51` when at least 90
   calls had run (two subagents at 40 each, plus 10 main). PostToolUse fires once per tool, and
   concurrently for parallel calls. Each hook process does load, increment, save on `sessions/<id>.json`,
   so overlapping writes drop counts. Stops therefore land at unpredictable points.
4. **The subagent budget is only text.** `SUBAGENT_STEP_LIMIT` (40) is appended to the brief
   (`reads.py:26`) but never enforced. One research subagent made 78 calls against a 40-call brief.
5. **Nothing resumes.** The checkpoint path is written into the stop reason, but no hook reads it back.
   "continue" works only because the same session still has the context, and the developer is never told
   to type it.

## 2. What Claude Code hooks can do (docs: code.claude.com/docs/en/hooks, plus issues)

| Mechanism | Developer sees | Claude sees | Caveats |
|---|---|---|---|
| `systemMessage` (any event) | Warning line in the CLI | No | Desktop: Pre/PostToolUse systemMessage "never rendered" (#77518). VS Code: SessionStart not rendered (#76736), others unverified. Pre/PostToolUse systemMessage dropped without `hookSpecificOutput` (#40380), so always send both. |
| PostToolUse `decision: "block"` | "hook blocking error" | Reason next to the tool result | Stops nothing. This is today's mechanism. |
| PostToolUse `continue: false` + `stopReason` | `stopReason` | `stopReason` on the next turn | Real stop. Ignored through the Agent SDK control protocol (#29991). |
| **PostToolBatch** `decision: "block"` or `continue: false` | "a warning in the transcript" | Stays in the conversation | Fires once per batch, before the next model call, and "stops the agentic loop". Closest thing to a native pause. Minimum version unverified, so test on 2.1.291. |
| PreToolUse deny | Denied call | The reason | Blocks the call before it runs. A community "governor" plugin uses this at budget so that "the model keeps its voice". |
| Stop hook `hookSpecificOutput.additionalContext` | "Stop hook feedback", not an error | The text, turn continues | Gets `last_assistant_message` and `stop_hook_active`. Capped at 8 consecutive continuations (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). Several loop bugs in the changelog, so use at most once. |
| UserPromptSubmit `hookSpecificOutput.additionalContext` | Nothing ("Neither channel produces a visible transcript entry") | The text | Capped at 10,000 chars (overflow becomes a file path plus the first 2,000). The top-level form is dropped (#96193). A 30 s timeout drops it (#66649). Phrase it as facts, not commands. |
| `terminalSequence` (OSC 9/99/777, BEL) | Desktop notification or bell | No | Interactive sessions only. |

Correction to the earlier write-up: the "Claude reached its tool-use limit for this turn [Continue]" banner
belongs to Claude Desktop and claude.ai (API `pause_turn`, 10 server-tool iterations), not the Claude Code
CLI (#33969). The CLI has no native Continue to reuse, and `--max-turns` is print-mode only.

## 3. How other coding agents handle their limit

| Product | Limit | What the user sees | Continue | Lesson |
|---|---|---|---|---|
| Cursor (old) | 25 tool calls | "Note: we default stop the agent after 25 tool calls. Please ask the agent to continue manually." | Typing "continue", later a Resume button. Costs another request. | "Highly annoying having to babysit the agent". |
| Copilot (VS Code) | `chat.agent.maxRequests`, 25 | "Continue to iterate?": "Copilot has been working on this problem for a while. It can continue to iterate, or you can send a new message to refine your prompt." | Continue raises the limit ×1.5 in the same turn | Bounded extension. Blind auto-extend hid loops (#336767). No off switch (#260814). |
| Roo Code | `allowedMaxRequests`, `allowedMaxCost` (can be Unlimited) | "Roo has reached the auto-approved limit of N API request(s). Would you like to reset the count and proceed with the task?" | One "Reset and Continue" button | Visible, says why, one click, same task. |
| Cline | Max requests (about 20) | "Cline has auto-approved 20 API requests …" | Approve | **Removed in v3.35**: "adding complexity without providing meaningful value". |
| Gemini CLI | `maxSessionTurns` (default unlimited) and loop detection | Turn cap: "Please update this limit in your setting.json file." Loop: a dialog offering to keep or disable detection. | No continue on the cap | Give the user a choice when a loop is detected. A hard cap with no continue is disliked. |
| Goose | `GOOSE_MAX_TURNS`, 1000 | "I've reached the maximum number of actions I can do without user input. Would you like me to continue?" | Answer yes | The message replaced a final answer (#12582). |
| OpenHands | `max_iterations` | GUI: "Please click on resume button…". CLI: `RuntimeError` crash | GUI only | The CLI crash is the anti-pattern. |
| Zed | 25 consecutive tool uses | "Consecutive tool use limit reached" + Continue | Continue (costs a prompt) | Added a toast and sound later. Continuing that charges and does nothing is rejected (#31754). |
| Windsurf | 20 to 40 calls per prompt (unverified) | Continue button | Each continue costs a credit. `autoContinue` setting. | Unverified details. |

Users accept: an in-place notice that says why, one action that continues the same task, a setting
including "off", and a bounded extension. Users reject: silent stops, "ask it to continue manually",
crashes, continues that cost extra or do nothing, and auto-extend that hides loops.

## 4. Patterns from agent frameworks and Anthropic guidance

- **Warn before the wall.** LangGraph's `RemainingSteps` "allows for graceful degradation", and its
  recommended "proactive approach" wraps up when `remaining <= 2`.
- **One wrap-up pass at the cap.** LangChain `early_stopping_method="generate"`, CrewAI ("you MUST give
  your absolute best final answer"), and OpenAI Agents SDK `error_handlers={"max_turns": …}`. Report the
  result as a distinct paused status (like the Agent SDK's `error_max_turns`), never as a finished answer.
- **Resume the same thread.** LangGraph reuses `thread_id`, AutoGen calls `run()` again without a new
  task, and the OpenAI SDK uses `to_state()`. Budgets restart per run, so save the checklist, not the
  counter.
- **Guard against false completion.** Anthropic's long-running harness post: a later instance "would
  look around, see that progress had been made, and declare the job done". Mitigations: a structured
  checklist whose items flip only after verification, reading the progress file and git log first,
  smoke-testing before new work, and committing in a clean state.

## 5. Recommended design

**A. Count correctly first.** The miscounting is why stops feel random.
1. Key the turn counter by `agent_id` when present. Each subagent gets its own enforced budget
   (`subagent_call_budget`). The main budget counts only main-agent calls. Subagent totals are logged
   separately for the dashboard.
2. Serialize state writes with `fcntl.flock` around load, modify, save of `sessions/<id>.json`.
3. Consider counting in PostToolBatch (one process per batch). The lock is still needed for parallel
   subagents.

**B. Make the pause visible on every surface.**
1. CLI: `systemMessage`, sent alongside `hookSpecificOutput`, at the warning and at the pause, plus
   `terminalSequence` (OSC 9 notification and BEL).
2. Desktop and VS Code may drop hook text, so Claude's own final message is the only channel that works
   everywhere. C.3 enforces it.
3. Wording says *paused*, never "exhausted" or "error". It gives the cause, what is saved, and two
   options: `continue` (same session, re-reads about N k tokens per step) or `/clear`, then resume from
   the checkpoint (cheaper).

**C. Graceful wrap-up instead of a PostToolUse block.**
1. Early warning with a remaining count, at stop minus 10 calls or 80% of the token limit: "about 10
   calls left; finish the current item; keep the checklist at `<path>` current."
2. At the cap: PreToolUse **deny** for non-exempt tools (widen the matcher from `Agent|Read|Bash|WebFetch`).
   Write stays allowed for the checklist. The reason asks Claude to end with the pause notice.
3. Stop hook: if the turn is paused, `stop_hook_active` is false, and `last_assistant_message` lacks the
   checkpoint path, send `hookSpecificOutput.additionalContext` once (non-error) asking for the notice.
   Never `decision: "block"`.
4. Backstop: after 3 denied calls, PostToolBatch `{"continue": false, "stopReason": "<pause notice>"}`.

**D. A resume that actually resumes.**
1. Save `paused: {checkpoint, steps, reread, at}` in session state. Kiasi also writes a mechanical
   checkpoint itself (original prompt, files edited, last todo list, last assistant text), so a resume
   works even if Claude never wrote one.
2. On the next prompt, UserPromptSubmit injects the checkpoint (facts, path plus up to 2,000 chars) and
   shows `systemMessage` "Kiasi: resuming from `<path>`".
3. Resume rules: read the checklist and `git status` first; items stay open until verified; do not
   declare done while any item is unverified; end with "n of m verified".
4. SessionStart (a new session or after `/clear`): if the project's last turn paused unresumed, show it
   and inject the pointer.

**E. Settings.**
1. `turn_budget_mode`: `pause` (default) | `warn` | `off`.
2. Make `turn_stop_tokens` and `turn_warn_tokens` configurable in `PROJECT_KEYS` and as env options.
   Derive the warning from the stop unless it is set.
3. No auto-continue (it hides loops).

**F. Measure.** Log `turn_pause`, `turn_resume`, and abandoned pauses, and show the resume rate on the
dashboard (`scripts/reports/lens.py`).

## 6. Tests to run before building on any assumption (CLI 2.1.291, VS Code, Desktop)

1. A PreToolUse deny with `systemMessage` and `hookSpecificOutput`: is the text visible on each surface?
2. PostToolBatch `continue: false`: does the loop stop, is `stopReason` shown, and does "continue" keep
   the context?
3. A Stop hook with `additionalContext` reading `last_assistant_message`: does it render as feedback, not
   an error?
4. Three parallel subagents: the main counter is unaffected, each subagent stops at its own budget, and
   the counts match the transcript's tool_use count exactly.
5. Resume injection after a pause, and after `/clear` through SessionStart.
Existing unit tests: `tests/test_turn.py`, `tests/test_limits.py`.

## 7. Decisions for the owner

1. The default mode for the team: `pause` or `warn`.
2. Whether subagent calls should count toward the parent turn at all. Recommended: no, with a separate
   enforced budget.
3. Whether plain "continue" resumes automatically, or only an explicit `kiasi resume`.

## Sources

Claude Code: https://code.claude.com/docs/en/hooks · https://code.claude.com/docs/en/cli-reference ·
https://code.claude.com/docs/en/changelog · https://platform.claude.com/docs/en/agent-sdk/hooks ·
https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons ·
github.com/anthropics/claude-code issues #29991 #31301 #33969 #40380 #49063 #50542 #66649 #74299 #76736 #77518 #96193 #96699 ·
https://github.com/tanvoid0/claude-code-plugins

Other agents: https://forum.cursor.com/t/how-to-continue-when-25-tool-call-limit-is-reached/62836 ·
https://forum.cursor.com/t/increase-the-25-tool-calls-limit/72553 ·
https://github.com/microsoft/vscode-copilot-chat/blob/main/src/extension/intents/node/toolCallingLoop.ts ·
https://github.com/microsoft/vscode/issues/336767 · https://docs.windsurf.com/windsurf/cascade/cascade ·
https://cline.bot/blog/cline-v3-35 · https://github.com/cline/cline/issues/3480 ·
https://github.com/RooCodeInc/Roo-Code/pull/3631 · https://github.com/RooCodeInc/Roo-Code/pull/8965 ·
https://raw.githubusercontent.com/RooCodeInc/Roo-Code/main/webview-ui/src/i18n/locales/en/chat.json ·
https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/ui/components/LoopDetectionConfirmation.tsx ·
https://github.com/openai/codex/issues/12336 · https://goose-docs.ai/docs/guides/environment-variables/ ·
https://github.com/aaif-goose/goose/issues/12582 ·
https://raw.githubusercontent.com/OpenHands/OpenHands/0.47.0/openhands/controller/agent_controller.py ·
https://github.com/zed-industries/zed/issues/31754 ·
https://github.com/Aider-AI/aider/blob/main/aider/coders/base_coder.py

Frameworks and guidance: https://docs.langchain.com/oss/python/langgraph/graph-api ·
https://docs.langchain.com/oss/python/langgraph/interrupts · https://docs.langchain.com/oss/python/langchain/middleware/built-in ·
https://docs.crewai.com/en/concepts/agents · https://openai.github.io/openai-agents-python/running_agents/ ·
https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html ·
https://platform.claude.com/docs/en/agent-sdk/agent-loop · https://platform.claude.com/docs/en/agent-sdk/sessions ·
https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents ·
https://www.anthropic.com/engineering/harness-design-long-running-apps
