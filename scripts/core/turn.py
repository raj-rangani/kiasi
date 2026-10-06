from core import constants
from core.caps import failure_label, handle_tool_output, leading_command
from core.events import load_session, log_event, save_session
from core.reads import handle_pre_tool, track_reads
from core.transcript import caller_key, caller_transcript, current_context_tokens, fmt_k, fmt_m, is_short_reply, is_system_prompt, is_task_prompt, prompt_text, tail_entries


def steps_per_prompt(entries):
    counts = []
    steps = 0
    started = False
    for entry in entries:
        if entry.get("type") == "assistant":
            steps += started
            continue
        if entry.get("type") != "user" or is_system_prompt(prompt_text(entry)):
            continue
        if started:
            counts.append(steps)
        started = True
        steps = 0
    if started:
        counts.append(steps)
    return counts[-constants.REREAD_RECENT_PROMPTS:]


def reread_check(prompt, tokens, entries, state):
    if tokens < constants.REREAD_ASK_TOKENS or not is_task_prompt(prompt) or is_short_reply(prompt):
        return None
    if tokens - state.get("reread_asked_at", 0) < constants.REREAD_ASK_STEP_TOKENS:
        return None
    counts = steps_per_prompt(entries)
    mean_steps = max(constants.REREAD_MIN_STEPS, round(sum(counts) / len(counts))) if counts else constants.REREAD_MIN_STEPS
    if counts and sum(counts) / len(counts) < constants.REREAD_MIN_STEPS:
        return None
    here = tokens * mean_steps
    delegated = tokens * constants.REREAD_MAIN_TURNS_WHEN_DELEGATED + mean_steps * constants.REREAD_SUBAGENT_MEAN_TOKENS
    mode = "delegate" if delegated <= here * constants.REREAD_DELEGATE_RATIO else "here"
    if mode == "delegate":
        rule = (
            f"(3) otherwise delegate without asking: write a self-contained brief (goal, files by absolute path, done condition, constraints), "
            f"make one Agent call with subagent_type general-purpose, never fork, and relay its summary. Do not ask the user whether to delegate."
        )
        verdict = "Claude delegates unless the work needs this conversation."
    else:
        rule = f"(3) otherwise do it here within the turn budget of {constants.TURN_STOP_STEPS} tool calls, batching commands and running each test suite once."
        verdict = "Delegation would not pay; Claude works here within the turn budget."
    context = (
        f"kiasi re-read check: the context is {fmt_k(tokens)} tokens and every step of this turn re-reads all of it. "
        f"Recent prompts in this session took about {mean_steps} steps each, so doing this work here costs about {fmt_m(here)} re-read tokens; "
        f"one subagent with a fresh context would cost about {fmt_m(delegated)}. Decide before your first tool call: "
        f"(1) if the prompt is answerable from what is already in the conversation, answer with no tool calls; "
        f"(2) if the work needs back-and-forth with the user or the exact state of this conversation, do it here; " + rule
    )
    message = f"kiasi: context {fmt_k(tokens)}, about {mean_steps} steps per prompt lately: roughly {fmt_m(here)} tokens re-read if done here, {fmt_m(delegated)} in a subagent. {verdict}"
    return {"context": context, "message": message, "steps": mean_steps, "here": here, "delegated": delegated, "mode": mode}


def checkpoint_path(key, turn):
    # Every checklist Write must create a file: Claude Code refuses to overwrite one outside the
    # working directory without a read record, and a hook compaction clears those records.
    stem = f"{key[:8]}-{turn.get('index', 0)}"
    path, number = constants.CHECKPOINT_DIR / f"{stem}.md", 1
    while path.exists():
        number += 1
        path = constants.CHECKPOINT_DIR / f"{stem}-{number}.md"
    return path


def turn_guard(payload, tokens):
    tool_name = payload.get("tool_name", "")
    session_id = payload.get("session_id", "")
    key = caller_key(payload)
    # A subagent has its own budget and cannot start another subagent, so it hands what remains back to its caller.
    subagent = bool(payload.get("agent_id"))
    if subagent:
        scope, warn_steps, stop_steps = "subagent", constants.SUBAGENT_STEP_LIMIT // 2, constants.SUBAGENT_STEP_LIMIT
    else:
        scope, warn_steps, stop_steps = "turn", constants.TURN_WARN_STEPS, constants.TURN_STOP_STEPS
    state = load_session(session_id)
    turn = state.setdefault("turns", {}).setdefault(key, {"steps": 0, "reread": 0, "warned": False, "stopped": False, "index": state.get("prompts", 0)})
    turn["steps"] += 1
    turn["reread"] += tokens
    state["turn_budget"] = {"warn": constants.TURN_WARN_STEPS, "stop": constants.TURN_STOP_STEPS}
    result = None
    over_stop = turn["steps"] >= stop_steps or turn["reread"] >= constants.TURN_STOP_TOKENS
    over_warn = turn["steps"] >= warn_steps or turn["reread"] >= constants.TURN_WARN_TOKENS
    checkpoint = checkpoint_path(key, turn)
    allowed = "Write" if subagent else "Write and Agent"
    if over_stop and tool_name not in constants.TURN_EXEMPT_TOOLS and (not turn["stopped"] or (turn["steps"] - turn["stopped"]) % constants.TURN_REMIND_STEPS == 0):
        first = not turn["stopped"]
        if first:
            # A pause, not a PostToolUse block: that block came after the call had run, stopped nothing and showed the
            # developer a hook error. paused_call refuses the calls that follow, before they run.
            turn.update(stopped=turn["steps"], checkpoint=str(checkpoint), context=tokens)
            hand_off = ("end with your reply to the caller: what is done, what is verified, what remains and the checklist path" if subagent else
                        "either make one Agent call with subagent_type general-purpose whose brief is that checklist path plus the done condition, "
                        "or end the turn reporting what is done, what is verified and what remains. If work remains when the turn ends, close "
                        f'your final message with this line for the developer: "{pause_line(turn)}"')
            context = (
                f"kiasi paused this {scope}: {turn['steps']} tool calls and {fmt_m(turn['reread'])} tokens re-read at a context of {fmt_k(tokens)}. "
                f"Every further call except {allowed} is refused. Write what remains as a checklist to {checkpoint}, then {hand_off}."
            )
        else:
            context = (f"kiasi: this {scope} is still paused ({turn['steps']} tool calls, {fmt_m(turn['reread'])} tokens). "
                       f"Write the checklist to {turn.get('checkpoint', checkpoint)} and {'reply to the caller' if subagent else 'end the turn or delegate'}.")
        result = {"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "PostToolUse"), "additionalContext": context}}
        if first and subagent:
            result["systemMessage"] = f"Kiasi paused a subagent at {turn['steps']} tool calls; it hands the remaining work back in {checkpoint}."
        elif first:
            result.update(systemMessage=pause_notice(turn), terminalSequence=pause_alert(turn))
        log_event({"event": "turn_stop", "session_id": session_id, "transcript": key, "steps": turn["steps"], "reread": turn["reread"], "context_tokens": tokens, "first": first, "subagent": subagent})
    elif over_warn and not turn["warned"]:
        turn["warned"] = True
        next_step = "reply to the caller with that path" if subagent else "end the turn with that path or hand the checklist to one general-purpose subagent"
        context = (
            f"kiasi {scope} budget: this {scope} has made {turn['steps']} tool calls and re-read {fmt_m(turn['reread'])} tokens at a context of {fmt_k(tokens)}. "
            f"The {scope} pauses at {stop_steps} tool calls or {fmt_m(constants.TURN_STOP_TOKENS)} tokens, and then every call except {allowed} is refused. "
            f"Before that: finish the item in progress, write the remaining items as a checklist to {checkpoint}, then {next_step}."
        )
        result = {"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "PostToolUse"), "additionalContext": context}}
        if not subagent:
            result["systemMessage"] = (f"Kiasi: this turn has made {turn['steps']} tool calls and re-read {fmt_m(turn['reread'])} tokens; it pauses at "
                                       f"{stop_steps} calls or {fmt_m(constants.TURN_STOP_TOKENS)} tokens. Claude was asked to finish the item in progress "
                                       f"and save the rest to {checkpoint}.")
        log_event({"event": "turn_warn", "session_id": session_id, "transcript": key, "steps": turn["steps"], "reread": turn["reread"], "context_tokens": tokens, "subagent": subagent})
    save_session(session_id, state)
    return result


def pause_line(turn):
    return (f"Paused by Kiasi at {turn['stopped']} tool calls; the rest is in {turn['checkpoint']}. "
            "Reply continue to resume here, or run /clear and ask me to resume from that file.")


def pause_notice(turn):
    """What the developer reads at a pause: the cause, where the rest goes and both ways to resume, never an error."""
    return (f"Kiasi paused this turn at {turn['stopped']} tool calls ({fmt_m(turn['reread'])} tokens re-read). "
            f"The remaining work goes to {turn['checkpoint']}. "
            f'Reply "continue" to resume in this session (each step re-reads about {fmt_k(turn.get("context", 0))} tokens), '
            "or run /clear and ask Claude to resume from that file, which is cheaper.")


def pause_alert(turn):
    # OSC 9 raises a desktop notification where the terminal supports it, and the BEL after it rings the bell
    # everywhere else. Claude Code passes on only OSC 0/1/2/9/99/777 and BEL, and only in interactive sessions.
    return f"\x1b]9;Kiasi paused this turn at {turn['stopped']} tool calls\x07\x07"


def paused_call(payload):
    """PreToolUse: once a turn or subagent is paused, refuse every call but the checklist Write and the hand-off."""
    if payload.get("tool_name") in constants.TURN_EXEMPT_TOOLS:
        return None
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    key = caller_key(payload)
    turn = state.get("turns", {}).get(key)
    if not turn or not turn.get("stopped"):
        return None
    subagent = bool(payload.get("agent_id"))
    turn["denied"] = turn.get("denied", 0) + 1
    turn.setdefault("checkpoint", str(checkpoint_path(key, turn)))
    save_session(session_id, state)
    scope, next_step = ("subagent", "end with your reply to the caller") if subagent else ("turn", "end the turn with the pause notice for the developer")
    reason = (f"kiasi paused this {scope} at {turn['stopped']} tool calls, so this call was not run. "
              f"Write what remains to {turn['checkpoint']} if you have not yet, then {next_step}.")
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
    if not subagent and turn["denied"] >= constants.TURN_DENY_BACKSTOP:
        # Claude kept calling tools after the pause, so the turn ends here. A subagent is only refused:
        # continue: false would end the developer's turn along with it.
        output.update({"continue": False, "stopReason": pause_notice(turn)})
    return output


def handle_pre_tool_use(payload):
    """PreToolUse for every tool: a paused turn refuses the call, then the routed tools get their own checks."""
    refused = paused_call(payload)
    if refused or payload.get("tool_name") not in constants.PRE_TOOL_HOOKED:
        return refused
    return handle_pre_tool(payload)


def pause_reminder(payload):
    """Stop: ask Claude once to tell the developer about the pause when its final message leaves it out."""
    # Hook messages may not show in the desktop app or VS Code; Claude's own reply shows everywhere. additionalContext
    # makes Claude reply once more and shows as hook context, where a Stop block would show as a hook error.
    if payload.get("stop_hook_active") or payload.get("agent_id"):
        return None
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    key = caller_key(payload)
    turn = state.get("turns", {}).get(key)
    if not turn or not turn.get("stopped") or turn.get("reminded") or turn.get("denied", 0) >= constants.TURN_DENY_BACKSTOP:
        return None
    turn.setdefault("checkpoint", str(checkpoint_path(key, turn)))
    if turn["checkpoint"] in (payload.get("last_assistant_message") or ""):
        return None
    turn["reminded"] = True
    save_session(session_id, state)
    context = (f"kiasi: this turn was paused at {turn['stopped']} tool calls, and your final message does not tell the developer. "
               f"If work remains, write it to {turn['checkpoint']} if you have not, then reply in two or three lines ending with: "
               f'"{pause_line(turn)}" If nothing remains, say so in one line. Make no other tool calls.')
    return {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": context}}


def handle_post_tool(payload):
    entries = tail_entries(caller_transcript(payload))
    guard = turn_guard(payload, current_context_tokens(entries))
    capped = handle_tool_output(payload)
    track_reads(payload, capped)
    if not guard:
        return capped
    if capped:
        guard.setdefault("hookSpecificOutput", {}).update(capped["hookSpecificOutput"])
    return guard


def loop_check(payload):
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    turn = state.setdefault("turns", {}).setdefault(caller_key(payload), {"steps": 0, "reread": 0, "warned": False, "stopped": False, "index": state.get("prompts", 0)})
    fails = turn.setdefault("fails", {})
    nudged = turn.setdefault("loop_nudged", [])
    label = failure_label(tool_name, tool_input)
    lead = f"{tool_name} {leading_command(tool_input.get('command') or '')}" if tool_name == "Bash" else label
    fails[label] = fails.get(label, 0) + 1
    if lead != label:
        fails[f"lead:{lead}"] = fails.get(f"lead:{lead}", 0) + 1
    hit = None
    if fails[label] >= constants.LOOP_SAME_FAILS and label not in nudged:
        hit = (label, label, fails[label])
    elif lead != label and fails[f"lead:{lead}"] >= constants.LOOP_LEAD_FAILS and f"lead:{lead}" not in nudged:
        hit = (f"lead:{lead}", f"{lead} (with different arguments)", fails[f"lead:{lead}"])
    if hit:
        nudged.append(hit[0])
        log_event({"event": "loop", "session_id": session_id, "label": hit[1], "count": hit[2]})
    save_session(session_id, state)
    return constants.LOOP_REASON.format(label=hit[1], count=hit[2]) if hit else None


def handle_tool_failure(payload):
    # A call refused at the pause never ran, so it is neither a step nor a failure to rethink.
    if payload.get("is_interrupt") or "kiasi paused this" in str(payload.get("error") or ""):
        return None
    guard = turn_guard(payload, current_context_tokens(tail_entries(caller_transcript(payload))))
    nudge = loop_check(payload)
    if not nudge:
        return guard
    output = guard or {}
    specific = output.setdefault("hookSpecificOutput", {"hookEventName": payload.get("hook_event_name", "PostToolUseFailure")})
    specific["additionalContext"] = "\n".join(part for part in (specific.get("additionalContext"), nudge) if part)
    return output
