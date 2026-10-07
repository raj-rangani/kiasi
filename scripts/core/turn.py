import json
import os
import re
import time
from pathlib import Path

from core import constants
from core.caps import failure_label, handle_tool_output, leading_command
from core import taskfile
from core.holdout import held_off
from core.events import checklist_folder, ensure_dirs, first_context_tokens, load_session, log_error, log_event, make_checklist_folder, now_iso, project_slug, save_session
from core.notify import notify_desktop
from core.reads import handle_pre_tool, track_reads
from core.transcript import caller_key, caller_transcript, current_context_tokens, edited_files, fmt_k, fmt_m, is_short_reply, is_system_prompt, is_task_prompt, last_task_prompt, prompt_text, response_id, tail_entries


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


def delegation_brief(path, cwd, goal=""):
    """The ready Agent prompt for a subagent that continues the task file at path."""
    goal = " ".join((goal or "").split())[: constants.DELEGATION_GOAL_CHARS] or "see the task file"
    brief = constants.DELEGATION_BRIEF.format(path=path, goal=goal, cwd=cwd or os.getcwd(), steps=constants.SUBAGENT_STEP_LIMIT)
    return constants.DELEGATION_BRIEF_LEAD + brief


def task_goal(path):
    try:
        return taskfile.parse(Path(path).read_text()).get("Goal", "")
    except OSError:
        return ""


def reread_check(prompt, tokens, entries, state, cwd=None, session_id=None):
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
        rule = f"(3) otherwise do it here within the turn budget of {constants.TURN_STOP_STEPS} steps, batching commands and running each test suite once."
        verdict = "Delegation would not pay; Claude works here within the turn budget."
    context = (
        f"kiasi re-read check: the context is {fmt_k(tokens)} tokens and every step of this turn re-reads all of it. "
        f"Recent prompts in this session took about {mean_steps} steps each, so doing this work here costs about {fmt_m(here)} re-read tokens; "
        f"one subagent with a fresh context would cost about {fmt_m(delegated)}. Decide before your first tool call: "
        f"(1) if the prompt is answerable from what is already in the conversation, answer with no tool calls; "
        f"(2) if the work needs back-and-forth with the user or the exact state of this conversation, do it here; " + rule
    )
    message = f"kiasi: context {fmt_k(tokens)}, about {mean_steps} steps per prompt lately: roughly {fmt_m(here)} tokens re-read if done here, {fmt_m(delegated)} in a subagent. {verdict}"
    result = {"context": context, "message": message, "steps": mean_steps, "here": here, "delegated": delegated, "mode": mode}
    if mode == "delegate" and cwd and session_id:
        goal = " ".join(prompt.split())[: constants.DELEGATION_GOAL_CHARS]
        path = taskfile.write(cwd, session_id, goal=goal, next_step=prompt[: constants.DELEGATION_PROMPT_CHARS])
        if path:
            result["context"] += "\n" + delegation_brief(path, cwd, goal)
            result["brief"] = True
    return result


def turn_file(key, turn, payload):
    """The task file is the turn's checklist; the old per-turn checkpoint path stays only for a session without an id."""
    session_id = payload.get("session_id", "")
    path = taskfile.path_for(payload.get("cwd"), session_id) if session_id else None
    return path or checkpoint_path(key, turn, payload.get("cwd"))


def checkpoint_path(key, turn, cwd=None):
    # Every checklist Write must create a file: Claude Code refuses to overwrite one without a read
    # record, and a hook compaction clears those records.
    folder = checklist_folder(cwd)
    stem = f"{key[:8]}-{turn.get('index', 0)}"
    path, number = folder / f"{stem}.md", 1
    while path.exists():
        number += 1
        path = folder / f"{stem}-{number}.md"
    return path


def turn_guard(payload, tokens, loose=False):
    tool_name = payload.get("tool_name", "")
    session_id = payload.get("session_id", "")
    key = caller_key(payload)
    # A subagent has its own budget and cannot start another subagent, so it hands what remains back to its caller.
    subagent = bool(payload.get("agent_id"))
    if subagent:
        scope, warn_steps, stop_steps = "subagent", constants.warn_steps(constants.SUBAGENT_STEP_LIMIT), constants.SUBAGENT_STEP_LIMIT
    else:
        scope, warn_steps, stop_steps = "turn", constants.turn_warn_steps(), constants.TURN_STOP_STEPS
    state = load_session(session_id)
    note_context(payload, state, tokens)
    turn = state.setdefault("turns", {}).setdefault(key, {"steps": 0, "reread": 0, "warned": False, "stopped": False, "index": state.get("prompts", 0)})
    turn["steps"] += 1
    turn["reread"] += tokens
    if loose:
        # Counted call by call, before a PostToolBatch has arrived: handle_tool_batch gives the extra counts back.
        turn["loose"] = turn.get("loose", []) + [tokens]
    mode = "off" if held_off(state, "turn_budget") else constants.TURN_BUDGET_MODE
    state["turn_budget"] = {"warn": constants.turn_warn_steps(), "stop": constants.TURN_STOP_STEPS, "mode": mode}
    result = None
    # With the budget off the calls are only counted; in warn mode it is reported once at the warning and once at the limit.
    over_stop = mode != "off" and (turn["steps"] >= stop_steps or turn["reread"] >= constants.TURN_STOP_TOKENS)
    over_warn = mode != "off" and (turn["steps"] >= warn_steps or turn["reread"] >= constants.turn_warn_tokens())
    # The path the warning named stays the turn's checklist: a later call would see the file written and name the next free one.
    checkpoint = Path(turn["checkpoint"]) if turn.get("checkpoint") else turn_file(key, turn, payload)
    allowed = "the task file's Write, Read and Edit" if subagent else "the task file's Write, Read and Edit, and Agent"
    ask = question_wanted(payload)
    if over_stop and mode == "warn":
        result = None if turn.get("over") else over_budget(payload, turn, scope, checkpoint, tokens)
    elif over_stop and tool_name not in constants.TURN_EXEMPT_TOOLS and not loads_question(payload) and (not turn["stopped"] or (turn["steps"] - turn["stopped"]) % constants.TURN_REMIND_STEPS == 0):
        first = not turn["stopped"]
        if first:
            # A pause, not a PostToolUse block: that block came after the call had run, stopped nothing and showed the
            # developer a hook error. paused_call refuses the calls that follow, before they run.
            turn.update(stopped=turn["steps"], checkpoint=str(checkpoint), context=tokens)
            make_checklist_folder(checkpoint)
            if not subagent:
                record_pause(payload, turn, state)
            if subagent:
                hand_off = "end with your reply to the caller: what is done, what is verified, what remains and the task file path"
            elif ask:
                allowed = "the task file's Write, Read and Edit, Agent and the one question below"
                hand_off = (f"{pause_question(turn, tokens, delegation_brief(checkpoint, payload.get('cwd'), task_goal(checkpoint)))} If that call cannot be made or is refused, end the turn reporting what is done, what is "
                            f'verified and what remains, and close your final message with this line for the developer: "{pause_line(turn)}"')
            else:
                hand_off = ("either make one Agent call with subagent_type general-purpose whose prompt is the ready brief below, "
                            f"{delegation_brief(checkpoint, payload.get('cwd'), task_goal(checkpoint))} "
                            "or end the turn reporting what is done, what is verified and what remains. If work remains when the turn ends, close "
                            f'your final message with this line for the developer: "{pause_line(turn)}"')
            # A Write cannot overwrite a file Claude has not read, so a checklist written at the warning is updated, not written again.
            write = f"Bring the task file at {checkpoint} up to date (read it, then Edit it)" if checkpoint.exists() else f"Write the task file at {checkpoint} (goal, decisions, verified checklist, files, next step)"
            context = (
                f"kiasi paused this {scope}: {turn['steps']} steps and {fmt_m(turn['reread'])} tokens re-read at a context of {fmt_k(tokens)}. "
                f"Every further call except {allowed} is refused. {write}, then {hand_off}."
            )
        else:
            context = (f"kiasi: this {scope} is still paused ({turn['steps']} steps, {fmt_m(turn['reread'])} tokens). "
                       f"Update the task file at {turn.get('checkpoint', checkpoint)} and {'reply to the caller' if subagent else 'end the turn or delegate'}.")
        result = {"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "PostToolUse"), "additionalContext": context}}
        if first and subagent:
            result["systemMessage"] = f"Kiasi paused a subagent at {turn['steps']} steps; it hands the remaining work back in {checkpoint}."
        elif first:
            result.update(systemMessage=pause_notice(turn, ask), terminalSequence=pause_alert(turn))
            desktop_notice(payload, f"Kiasi paused this turn at {turn['stopped']} steps", ask)
        log_event({"event": "turn_stop", "session_id": session_id, "transcript": key, "steps": turn["steps"], "reread": turn["reread"], "context_tokens": tokens, "first": first, "subagent": subagent,
                   "checkpoint": turn.get("checkpoint", str(checkpoint))})
    elif over_warn and not turn["warned"]:
        turn.update(warned=True, checkpoint=str(checkpoint))
        make_checklist_folder(checkpoint)
        ask_now = ask and mode == "pause"
        if subagent:
            next_step = "reply to the caller with that path"
        elif ask_now:
            # Claude follows the warning and so seldom reaches the pause: the developer is asked here, or Claude would choose for them.
            turn["context"] = tokens
            allowed = "the task file's Write, Read and Edit, Agent and the question below"
            next_step = (f"when work remains, {pause_question(turn, tokens)} Do not choose for the developer. If that call cannot be made, "
                         "end the turn with that path or hand the task file to one general-purpose subagent")
        else:
            next_step = "end the turn with that path or hand the task file to one general-purpose subagent"
        # Each further call re-reads about the current context, so the tokens can run out before the calls do.
        left = max(1, min(stop_steps - turn["steps"], (constants.TURN_STOP_TOKENS - turn["reread"]) // max(tokens, 1)))
        limit = (f"it pauses at {stop_steps} steps or {fmt_m(constants.TURN_STOP_TOKENS)} tokens, and then every call except {allowed} is refused"
                 if mode == "pause" else
                 f"its budget is {stop_steps} steps or {fmt_m(constants.TURN_STOP_TOKENS)} tokens, which kiasi reports but does not enforce")
        context = (
            f"kiasi {scope} budget: about {left} steps left; the tool calls of one response are one step, so parallel calls count once. This {scope} has made {turn['steps']} steps and re-read {fmt_m(turn['reread'])} "
            f"tokens at a context of {fmt_k(tokens)}; {limit}. Finish the item in progress, update the task file at {checkpoint} (goal, decisions, verified checklist, files, next step), then {next_step}."
        )
        result = {"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "PostToolUse"), "additionalContext": context}}
        if not subagent:
            result["systemMessage"] = (f"Kiasi: about {left} steps left before this turn {'pauses' if mode == 'pause' else 'reaches its budget'} ({turn['steps']} made, {fmt_m(turn['reread'])} tokens "
                                       f"re-read). Claude was asked to finish the item in progress and keep the rest in {checkpoint}{', then to ask you how to go on' if ask_now else ''}.")
        log_event({"event": "turn_warn", "session_id": session_id, "transcript": key, "steps": turn["steps"], "reread": turn["reread"], "context_tokens": tokens, "subagent": subagent, "checkpoint": str(checkpoint)})
    save_session(session_id, state)
    return result


def over_budget(payload, turn, scope, checkpoint, tokens):
    """warn mode at the limit: say so once, to Claude and to the developer, but refuse nothing and save nothing to resume."""
    subagent = scope == "subagent"
    turn.update(over=turn["steps"], warned=True, checkpoint=str(checkpoint))
    make_checklist_folder(checkpoint)
    next_step = "reply to the caller with that path" if subagent else "end the turn with that path or hand the task file to one general-purpose subagent"
    context = (f"kiasi: this {scope} reached its budget at {turn['steps']} steps and {fmt_m(turn['reread'])} tokens re-read at a context of "
               f"{fmt_k(tokens)}. The budget is in warn mode, so no call is refused. Finish the item in progress, update the "
               f"task file at {checkpoint}, then {next_step}.")
    result = {"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "PostToolUse"), "additionalContext": context}}
    if not subagent:
        result["systemMessage"] = (f"Kiasi: this turn reached its budget at {turn['steps']} steps ({fmt_m(turn['reread'])} tokens re-read). "
                                   f"The budget is in warn mode, so nothing is refused; Claude was asked to wrap up and keep the rest in the task file {checkpoint}.")
    log_event({"event": "turn_over", "session_id": payload.get("session_id", ""), "transcript": caller_key(payload), "steps": turn["steps"],
               "reread": turn["reread"], "context_tokens": tokens, "subagent": subagent, "checkpoint": str(checkpoint)})
    return result


def pause_line(turn):
    return (f"Paused by Kiasi at {turn['stopped']} steps; the rest is in the task file {turn['checkpoint']}. "
            "Reply continue to resume here, or run /clear and then reply continue, which is cheaper.")


def pause_notice(turn, ask=False):
    """What the developer reads at a pause: the cause, where the rest goes and both ways to resume, never an error."""
    resume = (f'eply "continue" to resume in this session (each step re-reads about {fmt_k(turn.get("context", 0))} tokens), '
              'or run /clear and then reply "continue", which is cheaper.')
    return (f"Kiasi paused this turn at {turn['stopped']} steps ({fmt_m(turn['reread'])} tokens re-read). "
            f"The remaining work goes to the task file {turn['checkpoint']}. "
            + ("Claude will now ask how to go on: continue here, hand the rest to a subagent, or stop. To resume later instead, r" if ask else "R") + resume)


def backstop_notice(turn):
    return (f"Kiasi ended this turn: Claude kept calling tools after the pause at {turn['stopped']} steps. "
            'Reply "continue" to resume in this session, or run /clear and then reply "continue", which is cheaper.')


def pause_alert(turn):
    # OSC 9 raises a desktop notification where the terminal supports it, and the BEL after it rings the bell
    # everywhere else. Claude Code passes on only OSC 0/1/2/9/99/777 and BEL, and only in interactive sessions.
    return f"\x1b]9;Kiasi paused this turn at {turn['stopped']} steps\x07\x07"


def desktop_notice(payload, title, ask=False):
    """A desktop notification for the developer where the chat raises none (see core/notify.py), logged when it was sent."""
    project = os.path.basename(os.path.normpath(payload.get("cwd") or ""))
    how = ("Claude is asking how to go on: continue here, hand the rest to a subagent, or stop." if ask else
           'Reply "continue" to resume, or run /clear and then reply "continue", which is cheaper.')
    if notify_desktop(title, (f"In {project}. " if project not in ("", ".") else "") + how):
        log_event({"event": "desktop_notify", "session_id": payload.get("session_id", ""), "title": title})


def question_wanted(payload):
    """True when a pause asks the developer how to go on: their own turn, in a session that can show a question."""
    if payload.get("agent_id") or constants.PAUSE_QUESTION == "off":
        return False
    return constants.PAUSE_QUESTION == "always" or os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") in constants.PAUSE_QUESTION_APPS


def loads_question(payload):
    """True for the ToolSearch call that loads AskUserQuestion where Claude Code defers it: the pause question needs it first."""
    return (payload.get("tool_name") == "ToolSearch" and "AskUserQuestion" in str((payload.get("tool_input") or {}).get("query", ""))
            and question_wanted(payload))


def pause_question(turn, tokens, brief=""):
    """The AskUserQuestion call Claude makes at a pause, spelled out so that pause_answer can read the choice."""
    labels = constants.PAUSE_CHOICES
    reached = (f"Kiasi paused this turn at {turn['stopped']} steps" if turn.get("stopped") else
               f"Kiasi's turn budget is nearly used: {turn['steps']} of {constants.TURN_STOP_STEPS} steps")
    return (f'make one AskUserQuestion call with one question, the header "{constants.PAUSE_QUESTION_HEADER}", the question '
            f'"{reached} ({fmt_m(turn["reread"])} tokens re-read). How should the rest go on?" '
            f'and exactly these three options: "{labels["continue"]}" (keep going in this session with a fresh budget of {constants.TURN_STOP_STEPS} steps; '
            f'each step re-reads about {fmt_k(tokens)} tokens), "{labels["subagent"]}" (one subagent with a fresh context works through the task file{"; if chosen, use the ready brief below" if brief else ""}), '
            f'"{labels["stop"]}" (end the turn; replying continue later resumes it, and /clear first makes that cheaper). '
            "Kiasi reads the answer and tells you what to do next. If AskUserQuestion is not loaded yet, load it first with the ToolSearch "
            'query "select:AskUserQuestion", which is let through.' + (f"\n{brief}\n" if brief else ""))


def pause_answer(payload):
    """(choice, answer) for the developer's answer to the pause question: a key of PAUSE_CHOICES, or other for their
    own words. None when this AskUserQuestion call was not that question."""
    if payload.get("tool_name") != "AskUserQuestion" or payload.get("agent_id"):
        return None
    tool_input, response, labels = payload.get("tool_input") or {}, payload.get("tool_response"), constants.PAUSE_CHOICES
    asked = next((question for question in tool_input.get("questions") or [] if isinstance(question, dict) and (
        question.get("header") == constants.PAUSE_QUESTION_HEADER
        or {labels["continue"], labels["stop"]} <= {option.get("label") for option in question.get("options") or [] if isinstance(option, dict)})), None)
    if not asked:
        return None
    answers = (response.get("answers") if isinstance(response, dict) else None) or tool_input.get("answers") or {}
    answer = answers.get(asked.get("question")) if isinstance(answers, dict) else None
    if answer is None:
        # Not the answers object seen so far: the result text names only the chosen label, each in quotes.
        text = json.dumps({k: v for k, v in response.items() if k != "questions"}) if isinstance(response, dict) else str(response or "")
        answer = next((label for label in labels.values() if f'"{label}' in text), text)
    answer = str(answer).strip()
    for choice, label in labels.items():
        if answer.lower().startswith(label.lower()):
            return choice, answer
    return ("continue" if re.match(constants.RESUME_PATTERN, answer, re.I | re.S) else "other"), answer


def renew_turn(payload, state, turn):
    """The developer continued at the pause question: the pause is used up, as a "continue" prompt would use it, and
    the turn counts from zero again."""
    session_id = payload.get("session_id", "")
    paused = state.pop("paused", None)
    if paused:
        forget_paused_file(payload.get("cwd"), paused.get("checkpoint"))
        state["resumed"] = {"task": paused.get("task", ""), "files": paused.get("files", [])}
    if turn.get("stopped"):
        # Only a pause is resumed: a continue at the warning's question renews a budget that had not run out.
        try:
            checklist = os.path.getsize(turn["checkpoint"]) > 0
        except (KeyError, OSError):
            checklist = False
        log_event({"event": "turn_resume", "session_id": session_id, "mode": "resume", "asked": True, "checklist": checklist,
                   "paused_session": session_id, "steps": turn["stopped"]})
    # The question's own step is counted right after this, by this hook or by its batch: the fresh budget starts after it.
    turn.update(steps=-1, reread=0, warned=False, stopped=False, denied=0, denied_response=None, denied_at=0)
    for name in ("reminded", "context", "checkpoint", "loose"):
        turn.pop(name, None)


def pause_now(payload, state, turn, key, checkpoint):
    """The developer chose to stop or to delegate at the warning's question: the turn is paused from here, as it is at the budget."""
    turn.update(stopped=max(turn["steps"], 1), checkpoint=checkpoint)
    make_checklist_folder(checkpoint)
    record_pause(payload, turn, state)
    log_event({"event": "turn_stop", "session_id": payload.get("session_id", ""), "transcript": key, "steps": turn["stopped"], "reread": turn["reread"],
               "context_tokens": turn.get("context", 0), "first": True, "subagent": False, "chosen": True, "checkpoint": checkpoint})


def pause_choice(payload):
    """PostToolUse of the pause question: carry out the developer's choice. Continue renews the budget in place. A
    subagent or a stop pauses a turn that was only warned, and leaves a paused one paused."""
    found = pause_answer(payload)
    if not found:
        return None
    choice, answer = found
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    key = caller_key(payload)
    turn = state.get("turns", {}).get(key)
    # The question is put at the warning and again at the pause, so the turn it answers is warned or paused.
    if not turn or not (turn.get("stopped") or turn.get("warned")):
        return None
    paused = bool(turn.get("stopped"))
    checkpoint = turn.get("checkpoint") or str(turn_file(key, turn, payload))
    log_event({"event": "turn_choice", "session_id": session_id, "choice": choice, "steps": turn["stopped"] or turn["steps"], "paused": paused,
               "answers": type(payload.get("tool_response")).__name__})
    if paused:
        turn["checkpoint"] = checkpoint
    elif choice in ("subagent", "stop"):
        pause_now(payload, state, turn, key, checkpoint)
    output = {"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "PostToolUse")}}
    if choice == "continue":
        renew_turn(payload, state, turn)
        context = (f"kiasi: the developer chose to continue in this session, so this turn has a fresh budget of {constants.TURN_STOP_STEPS} steps. "
                   f"Carry on from the task file at {checkpoint}. An item stays open until you have verified it.")
        output["systemMessage"] = f"Kiasi: continuing in this session with a fresh budget of {constants.TURN_STOP_STEPS} steps."
    elif choice == "subagent":
        context = (f"kiasi: the developer chose a subagent. Make one Agent call with subagent_type general-purpose whose prompt is the ready brief below, then relay its summary and what remains, naming that path. This turn stays paused, "
                   "so every other call is refused.\n" + delegation_brief(checkpoint, payload.get("cwd"), task_goal(checkpoint)))
    elif choice == "stop":
        context = ("kiasi: the developer chose to stop. Make no more tool calls: report what is done, what is verified and what remains, and close "
                   f'your final message with this line: "{pause_line(turn)}"')
    elif not paused:
        context = (f'kiasi: the developer answered in their own words ("{answer[:200]}"). Do what they ask within the steps this turn has left; '
                   f"what remains after that goes to the task file at {checkpoint}.")
    else:
        context = (f'kiasi: the developer answered in their own words ("{answer[:200]}"), and this turn stays paused, so no call but Write and '
                   f'Agent will run. Answer them in your final message and close it with this line: "{pause_line(turn)}"')
    output["hookSpecificOutput"]["additionalContext"] = context
    save_session(session_id, state)
    return output


def checklist_call(payload, turn):
    """A Read or Edit of the turn's task file, let through at a pause so it is updated and not written blind."""
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if payload.get("tool_name") not in constants.TURN_CHECKLIST_TOOLS or not path or not turn.get("checkpoint"):
        return False
    here = os.path.abspath(os.path.expanduser(path))
    # A turn started before the task file named the checklist keeps its own checkpoint path: accepted for one release.
    named = [turn["checkpoint"]]
    if payload.get("session_id"):
        named.append(str(taskfile.path_for(payload.get("cwd"), payload["session_id"])))
    return any(here == os.path.abspath(os.path.expanduser(item)) for item in named)


def count_refusal(payload, turn):
    """Three parallel refused calls are one refused response, so a count comes once per response: by the id of the
    response the transcript names, else by time, as calls of one response arrive together."""
    here, now = response_id(payload), time.time()
    if here:
        same = turn.get("denied_response") == here
    else:
        same = now - turn.get("denied_at", 0) <= constants.REFUSAL_RESPONSE_SECONDS
    turn["denied_response"], turn["denied_at"] = here, now
    if not same:
        turn["denied"] = turn.get("denied", 0) + 1


def paused_call(payload):
    """PreToolUse: once a turn or subagent is paused, refuse every call but the task file Write and the hand-off."""
    # Only pause mode refuses, so a turn paused before the mode was changed is let go.
    if constants.TURN_BUDGET_MODE != "pause" or payload.get("tool_name") in constants.TURN_EXEMPT_TOOLS or loads_question(payload):
        return None
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    key = caller_key(payload)
    turn = state.get("turns", {}).get(key)
    if not turn or not turn.get("stopped") or checklist_call(payload, turn):
        return None
    subagent = bool(payload.get("agent_id"))
    count_refusal(payload, turn)
    if payload.get("tool_use_id"):
        # Marked here, so the batch knows a refused call by its id and not by what a tool's output says.
        turn["refused"] = [*turn.get("refused", []), payload["tool_use_id"]][-constants.TURN_CALLS_KEPT:]
    turn.setdefault("checkpoint", str(turn_file(key, turn, payload)))
    save_session(session_id, state)
    scope, next_step = ("subagent", "end with your reply to the caller") if subagent else ("turn", "end the turn with the pause notice for the developer")
    reason = (f"kiasi paused this {scope} at {turn['stopped']} steps, so this call was not run. "
              f"Bring the task file at {turn['checkpoint']} up to date (Read it, then Edit it; Write it if it is not there yet), then {next_step}.")
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
    if not subagent and turn.get("denied", 0) >= constants.TURN_DENY_BACKSTOP:
        # Claude kept calling tools after the pause, so the turn ends here. A subagent is only refused:
        # continue: false would end the developer's turn along with it. VS Code shows no stop reason, so the turn
        # would end there without a word; it does show a system message.
        output.update({"continue": False, "stopReason": pause_notice(turn), "systemMessage": backstop_notice(turn)})
        if turn.get("denied", 0) == constants.TURN_DENY_BACKSTOP:
            desktop_notice(payload, f"Kiasi ended this turn, paused at {turn['stopped']} steps")
    return output


def note_call(payload):
    """PreToolUse: keep the call's id in its turn, so a PostToolBatch that arrives after the next prompt cleared the turn is known."""
    call_id, session_id = payload.get("tool_use_id"), payload.get("session_id", "")
    if not call_id:
        return
    state = load_session(session_id)
    turn = (state.get("turns") or {}).get(caller_key(payload))
    if turn is not None:
        turn["calls"] = [*turn.get("calls", []), call_id][-constants.TURN_CALLS_KEPT:]
        save_session(session_id, state)


def handle_pre_tool_use(payload):
    """PreToolUse for every tool: a paused turn refuses the call, then the routed tools get their own checks."""
    note_call(payload)
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
    turn.setdefault("checkpoint", str(turn_file(key, turn, payload)))
    if turn["checkpoint"] in (payload.get("last_assistant_message") or ""):
        return None
    turn["reminded"] = True
    save_session(session_id, state)
    context = (f"kiasi: this turn was paused at {turn['stopped']} steps, and your final message does not tell the developer. "
               f"If work remains, update the task file at {turn['checkpoint']} if you have not, then reply in two or three lines ending with: "
               f'"{pause_line(turn)}" If nothing remains, say so in one line. Make no other tool calls.')
    return {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": context}}


def paused_file(cwd, session_id=""):
    """A project's pause record of one session: sessions of a project pause at the same time, and each keeps its own."""
    return constants.NOTES_DIR / f"{project_slug(cwd)}.paused.{re.sub(r'[^A-Za-z0-9_-]', '_', session_id or 'unknown')}.json"


def paused_files(cwd):
    """Every pause record of the project: one per session, and the single project record of earlier versions."""
    return sorted(constants.NOTES_DIR.glob(f"{project_slug(cwd)}.paused*.json"))


def forget_paused_file(cwd, checkpoint):
    """Remove the pause record of this checklist. True when this call removed it: of two sessions that resume one pause, the first wins."""
    removed = False
    for path in paused_files(cwd):
        try:
            if json.loads(path.read_text()).get("checkpoint") == checkpoint:
                path.unlink()
                removed = True
        except FileNotFoundError:
            continue  # another session just resumed it
        except (OSError, ValueError, AttributeError) as exc:
            log_error("paused_file", path=str(path), error=str(exc))
    return removed


def record_pause(payload, turn, state):
    """At a turn's pause, keep what a resume needs: this session's next prompt reads it from the state, a new session
    in the project from the paused file. The task and the edited files carry a resume when Claude wrote no checklist."""
    entries = tail_entries(payload.get("transcript_path"))
    if not entries and payload.get("transcript_path"):
        log_error("pause_no_task", session_id=payload.get("session_id", ""), error="transcript unreadable: the pause records no task")
    # A turn resumed with "continue" has no task prompt of its own: it keeps the task and files of the pause it resumed.
    resumed = state.get("resumed") or {}
    paused = {"checkpoint": turn["checkpoint"], "steps": turn["stopped"], "reread": turn["reread"], "at": now_iso(), "session_id": payload.get("session_id", ""),
              "task": (last_task_prompt(entries) or resumed.get("task", ""))[:constants.RESUME_TASK_CHARS],
              "files": list(dict.fromkeys([*resumed.get("files", []), *edited_files(entries)]))}
    task_file = taskfile.write(payload.get("cwd"), payload.get("session_id", ""), goal=paused["task"], files=paused["files"],
                               next_step=f"Resume the paused turn: bring the task file at {turn['checkpoint']} up to date, then work its first open item.")
    if task_file:
        paused["taskfile"] = turn["taskfile"] = str(task_file)
    state["paused"] = paused
    # The session's own state first: a pause record that cannot be written must not keep the budget from tripping.
    save_session(payload.get("session_id", ""), state)
    try:
        ensure_dirs()
        paused["recorded"] = True
        paused_file(payload.get("cwd"), payload.get("session_id", "")).write_text(json.dumps(paused))
    except OSError as exc:
        paused.pop("recorded")
        log_error("pause_record", session_id=payload.get("session_id", ""), error=str(exc))


def pause_elsewhere(payload):
    """SessionStart: a turn paused in another session of this project is offered here, and this session's first prompt resumes it."""
    session_id = payload.get("session_id", "")
    found = None
    for path in paused_files(payload.get("cwd")):
        try:
            paused = json.loads(path.read_text())
            age_days = (time.time() - time.mktime(time.strptime(paused["at"], "%Y-%m-%dT%H:%M:%S"))) / 86400
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if paused.get("session_id") != session_id and age_days <= constants.NOTE_MAX_AGE_DAYS and (not found or paused["at"] > found["at"]):
            found = paused
    if not found:
        return None
    # The newest pause of the project is offered; what is offered came from a record, which the first resume removes.
    paused = {**found, "recorded": True}
    state = load_session(session_id)
    state["paused"] = paused
    save_session(session_id, state)
    return paused


def resume_context(state, prompt, cwd):
    """UserPromptSubmit: the first prompt after a pause uses it up. A plain "continue" resumes from the checklist; any
    other prompt is only told where it is."""
    if is_task_prompt(prompt):
        # A new task: a later pause records it, not the work resumed before it.
        state.pop("resumed", None)
    paused = state.pop("paused", None)
    if not paused:
        return None
    if not forget_paused_file(cwd, paused["checkpoint"]) and paused.get("recorded"):
        return None  # its record is gone: another session of the project resumed this pause first
    try:
        with open(paused["checkpoint"]) as fh:
            checklist = fh.read()
        if "## Checklist" in checklist and not taskfile.parse(checklist).get("Checklist"):
            checklist = ""
    except OSError:
        checklist = ""
    when = f"kiasi paused a turn at {paused['steps']} steps on {paused['at'][:16].replace('T', ' ')}"
    follow_up = {"checklist": bool(checklist), "paused_session": paused.get("session_id", ""), "steps": paused["steps"]}
    saved = f"its remaining work is in the task file {paused['checkpoint']}" if checklist else (
        f"the task file {paused['checkpoint']} has no checklist yet" + (f'; its task was "{paused["task"]}"' if paused.get("task") else "")
        + (f"; files it edited: {', '.join(paused['files'])}" if paused.get("files") else ""))
    if not re.match(constants.RESUME_PATTERN, prompt.strip(), re.I | re.S):
        return {"mode": "pointer", "context": f"{when}; {saved}. If this prompt is about that work, start from there.", "log": follow_up}
    state["resumed"] = {"task": paused.get("task", ""), "files": paused.get("files", [])}
    if checklist:
        cut = "\n[cut here: read the rest from the file]" if len(checklist) > constants.RESUME_CHECKLIST_CHARS else ""
        saved += f":\n{checklist[:constants.RESUME_CHECKLIST_CHARS].rstrip()}{cut}\n"
        message = f"Kiasi: resuming from {paused['checkpoint']}"
    else:
        saved += ". "
        message = "Kiasi: resuming the paused turn from its task and edited files; the task file has no checklist yet."
    saved += f"The task file is {paused['taskfile']}. " if paused.get("taskfile") and paused["taskfile"] != paused["checkpoint"] else ""
    context = (f"{when}, and the developer asked to resume it: {saved}Check git status and the files involved first. An item stays "
               'open until you have verified it: do not call the work done while any item is unverified, and end with "n of m verified".')
    return {"mode": "resume", "context": context, "message": message, "log": follow_up}


def batch_scope(payload):
    return "subagent" if payload.get("agent_id") else "turn"


def batch_counted(payload):
    """True once Claude Code has sent a PostToolBatch for this kind of caller: its steps are counted there."""
    return batch_scope(payload) in load_session(payload.get("session_id", "")).get("batch_hook", [])


def handle_tool_batch(payload):
    """One step for the tool calls of one response: the next request re-reads the context once for all of them.
    PostToolBatch comes after the calls' own PostToolUse hooks, so until the first one arrives the calls are counted one
    by one, and that first batch gives the extra counts back."""
    calls = [call for call in payload.get("tool_calls") or [] if isinstance(call, dict)]
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    ids = [call["tool_use_id"] for call in calls if call.get("tool_use_id")]
    if ids and all(call_id in state.get("cleared_calls", []) for call_id in ids):
        return None  # the calls of a turn the next prompt cleared: not a step of the new one
    seen = state.setdefault("batch_hook", [])
    turn = (state.get("turns") or {}).get(caller_key(payload)) or {}
    # Calls refused at the pause never ran, so a batch of them is not a step. paused_call marked them by id.
    refused = turn.get("refused", [])
    ran = [call for call in calls if call.get("tool_use_id") not in refused]
    dirty = bool(refused)
    if dirty:
        turn["refused"] = [call_id for call_id in refused if call_id not in ids]
    if calls and not ran:
        # paused_call counted these refusals, once per response, whether or not Claude Code sends a batch for them.
        if dirty:
            save_session(session_id, state)
        return None
    loose = turn.pop("loose", None)
    if batch_scope(payload) in seen and not loose:
        names = [call.get("tool_name", "") for call in ran]
        tool_name = next((name for name in names if name not in constants.TURN_EXEMPT_TOOLS), names[0] if names else "")
        if dirty:
            save_session(session_id, state)
        return turn_guard({**payload, "tool_name": tool_name}, current_context_tokens(tail_entries(caller_transcript(payload))))
    if batch_scope(payload) not in seen:
        seen.append(batch_scope(payload))
    if loose and not turn.get("stopped"):
        # A call's hook can run before its response is in the transcript and read no context, so keep the largest reading.
        turn["steps"] -= len(loose) - 1
        turn["reread"] -= sum(loose) - max(loose + [current_context_tokens(tail_entries(caller_transcript(payload)))])
    save_session(session_id, state)
    return None


def note_context(payload, state, tokens):
    """The session's context numbers for the status line and the prompt notices: the latest total, the floor and the time of the call."""
    if payload.get("agent_id"):
        return
    state.update(context_tokens=tokens, last_call_at=now_iso())
    if not state.get("context_floor"):
        floor = first_context_tokens(payload.get("transcript_path")) or (constants.CONTEXT_FLOOR_DEFAULT if tokens else 0)
        if floor:
            state["context_floor"] = floor


def handle_post_tool(payload):
    entries = tail_entries(caller_transcript(payload))
    # The answer to the pause question comes first: a continue renews the budget before this call is counted against it.
    chosen = pause_choice(payload)
    counted = batch_counted(payload)
    guard = None if counted else turn_guard(payload, current_context_tokens(entries), loose=True)
    if counted and not payload.get("agent_id"):
        state = load_session(payload.get("session_id", ""))
        note_context(payload, state, current_context_tokens(entries))
        save_session(payload.get("session_id", ""), state)
    guard = chosen or guard
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
    turn = (load_session(payload.get("session_id", "")).get("turns") or {}).get(caller_key(payload)) or {}
    if payload.get("tool_use_id") in turn.get("refused", []):
        return None
    entries = tail_entries(caller_transcript(payload))
    guard = None if batch_counted(payload) else turn_guard(payload, current_context_tokens(entries), loose=True)
    nudge = loop_check(payload)
    if not nudge:
        return guard
    output = guard or {}
    specific = output.setdefault("hookSpecificOutput", {"hookEventName": payload.get("hook_event_name", "PostToolUseFailure")})
    specific["additionalContext"] = "\n".join(part for part in (specific.get("additionalContext"), nudge) if part)
    return output
