

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from helpers import SCRIPTS, KiasiTestCase
from core import constants  # noqa: E402
from core import events  # noqa: E402
from core import prompt  # noqa: E402
from core import reads  # noqa: E402
from core import session  # noqa: E402
from core import turn  # noqa: E402


class TestTurnGuard(KiasiTestCase):
    def test_pauses_at_the_budget_with_a_notice_instead_of_an_error(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "sess-turn",
                   "transcript_path": str(self.tmp / "missing-transcript.jsonl")}
        results = [turn.turn_guard(payload, 100_000) for _ in range(constants.TURN_STOP_STEPS)]
        warning, paused = results[constants.turn_warn_steps() - 1], results[-1]
        self.assertIn(f"about {constants.TURN_WARN_MARGIN} tool calls left", warning["systemMessage"])
        self.assertNotIn("decision", paused, "a PostToolUse block shows the developer a hook error and stops nothing")
        checkpoint = events.load_session("sess-turn")["turns"]["missing-transcript"]["checkpoint"]
        self.assertIn(checkpoint, paused["hookSpecificOutput"]["additionalContext"])
        notice = paused["systemMessage"]
        for part in (f"Kiasi paused this turn at {constants.TURN_STOP_STEPS} tool calls", checkpoint, 'Reply "continue"', "/clear", "100k tokens"):
            self.assertIn(part, notice)
        self.assertNotIn("exhausted", notice + paused["hookSpecificOutput"]["additionalContext"])
        # Claude Code passes on only OSC 0/1/2/9/99/777 and BEL, and an OSC 9 body may not start with a digit.
        self.assertEqual(paused["terminalSequence"], f"\x1b]9;Kiasi paused this turn at {constants.TURN_STOP_STEPS} tool calls\x07\x07")

    def test_a_paused_turn_refuses_calls_then_ends_the_turn(self):
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-pause", "transcript_path": str(self.tmp / "s-pause.jsonl")}
        pre = {**post, "hook_event_name": "PreToolUse", "tool_name": "Edit"}
        for _ in range(constants.TURN_STOP_STEPS - 1):
            turn.turn_guard(post, 1000)
        self.assertIsNone(turn.handle_pre_tool_use(pre), "nothing is refused before the pause")
        turn.turn_guard(post, 1000)
        for tool in ("Write", "Agent", "TodoWrite"):
            self.assertIsNone(turn.paused_call({**pre, "tool_name": tool}), f"{tool} stays allowed for the checklist and the hand-off")
        refused = [turn.handle_pre_tool_use(pre) for _ in range(constants.TURN_DENY_BACKSTOP)]
        self.assertEqual({r["hookSpecificOutput"]["permissionDecision"] for r in refused}, {"deny"})
        reason = refused[0]["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("so this call was not run", reason)
        self.assertIn(events.load_session("s-pause")["turns"]["s-pause"]["checkpoint"], reason)
        self.assertFalse([r for r in refused[:-1] if "continue" in r], "the first refusals leave Claude room to write the checklist")
        self.assertIs(refused[-1]["continue"], False)
        self.assertIn("Kiasi paused this turn", refused[-1]["stopReason"])
        prompt.handle_prompt({"prompt": "continue", "session_id": "s-pause", "transcript_path": post["transcript_path"]})
        self.assertIsNone(turn.handle_pre_tool_use(pre), "the next prompt starts a fresh budget")

    def test_a_paused_subagent_is_refused_but_never_ends_the_developers_turn(self):
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-subpause",
                "transcript_path": str(self.tmp / "s-subpause.jsonl"), "agent_id": "a5e0c3d9b71f2a846"}
        paused = [turn.turn_guard(post, 1000) for _ in range(constants.SUBAGENT_STEP_LIMIT)][-1]
        self.assertIn("kiasi paused this subagent", paused["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("terminalSequence", paused, "the developer's turn is still running")
        refused = [turn.paused_call({**post, "hook_event_name": "PreToolUse"}) for _ in range(constants.TURN_DENY_BACKSTOP + 1)]
        self.assertEqual({r["hookSpecificOutput"]["permissionDecision"] for r in refused}, {"deny"})
        self.assertFalse([r for r in refused if "continue" in r], "continue: false would end the developer's turn too")
        main = {k: v for k, v in post.items() if k != "agent_id"}
        self.assertIsNone(turn.paused_call({**main, "hook_event_name": "PreToolUse"}))

    def test_a_refused_call_counts_as_neither_a_step_nor_a_failure(self):
        payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "make"}, "session_id": "s-refused",
                   "transcript_path": str(self.tmp / "s-refused.jsonl"), "error": "kiasi paused this turn at 60 tool calls, so this call was not run."}
        self.assertEqual([turn.handle_tool_failure(payload) for _ in range(constants.LOOP_SAME_FAILS)], [None] * constants.LOOP_SAME_FAILS)
        self.assertEqual(events.load_session("s-refused").get("turns", {}), {})

    def test_the_hook_refuses_any_tool_once_the_turn_is_paused(self):
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-hook", "transcript_path": str(self.tmp / "s-hook.jsonl")}
        for _ in range(constants.TURN_STOP_STEPS):
            turn.turn_guard(post, 1000)
        hook = subprocess.run([sys.executable, str(SCRIPTS / "kiasi.py")], input=json.dumps({**post, "hook_event_name": "PreToolUse", "tool_name": "Grep"}),
                              capture_output=True, text=True, env={**os.environ, "CLAUDE_PLUGIN_DATA": str(self.tmp)}, timeout=60)
        self.assertEqual(hook.stderr, "")
        self.assertEqual(json.loads(hook.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        matchers = [group.get("matcher") for group in json.loads((SCRIPTS.parent / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]]
        self.assertEqual(matchers, [""], "PreToolUse has to see every tool to refuse it")

    def test_a_paused_turn_that_ends_without_the_notice_is_reminded_once(self):
        def paused(sid):
            post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": sid, "transcript_path": str(self.tmp / f"{sid}.jsonl")}
            for _ in range(constants.TURN_STOP_STEPS):
                turn.turn_guard(post, 1000)
            return {**post, "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "The parser is done."}

        def checkpoint(sid):
            return events.load_session(sid)["turns"][sid]["checkpoint"]

        quiet = {"hook_event_name": "Stop", "session_id": "s-quiet", "transcript_path": str(self.tmp / "s-quiet.jsonl"), "stop_hook_active": False}
        self.assertIsNone(session.handle_stop(quiet), "a turn that was never paused ends quietly")
        stop = paused("s-remind")
        reminder = session.handle_stop(stop)
        self.assertNotIn("decision", reminder, "a Stop block shows the developer a hook error")
        self.assertEqual(reminder["hookSpecificOutput"]["hookEventName"], "Stop")
        self.assertIn(f"Paused by Kiasi at {constants.TURN_STOP_STEPS} tool calls; the rest is in {checkpoint('s-remind')}",
                      reminder["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(session.handle_stop(stop), "the reminder is sent once")
        told = paused("s-told")
        self.assertIsNone(session.handle_stop({**told, "last_assistant_message": f"Paused by Kiasi; the rest is in {checkpoint('s-told')}."}))
        self.assertIsNone(session.handle_stop({**paused("s-active"), "stop_hook_active": True}), "another Stop hook already made Claude reply")
        ended = paused("s-ended")
        for _ in range(constants.TURN_DENY_BACKSTOP):
            turn.paused_call({**ended, "hook_event_name": "PreToolUse"})
        self.assertIsNone(session.handle_stop(ended), "the refused calls already ended the turn with the notice")

    def _pause(self, sid, cwd, transcript=None):
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": sid, "cwd": cwd,
                "transcript_path": str(transcript or self.tmp / f"{sid}.jsonl")}
        for _ in range(constants.TURN_STOP_STEPS):
            turn.turn_guard(post, 1000)
        return Path(events.load_session(sid)["turns"][Path(post["transcript_path"]).stem]["checkpoint"])

    def _prompt(self, sid, text, cwd):
        return prompt.handle_prompt({"hook_event_name": "UserPromptSubmit", "prompt": text, "session_id": sid, "cwd": cwd,
                                     "transcript_path": str(self.tmp / f"{sid}-next.jsonl")}) or {}

    def test_continue_after_a_pause_resumes_once_from_the_checklist(self):
        cwd = str(self.tmp / "app")
        checkpoint = self._pause("s-resume", cwd)
        checkpoint.write_text("- [x] parser\n- [ ] tests for the parser\n")
        resumed = self._prompt("s-resume", "continue", cwd)
        context = resumed["hookSpecificOutput"]["additionalContext"]
        for part in (str(checkpoint), "- [ ] tests for the parser", "git status", "n of m verified"):
            self.assertIn(part, context)
        self.assertIn(f"Kiasi: resuming from {checkpoint}", resumed["systemMessage"])
        self.assertNotIn(str(checkpoint), json.dumps(self._prompt("s-resume", "continue", cwd)), "a pause is resumed once")

    def test_only_a_plain_continue_resumes_and_any_other_prompt_is_told_where_the_work_is(self):
        paused = {"checkpoint": str(self.tmp / "never-written.md"), "steps": 60, "reread": 0, "at": "2026-10-06T18:20:00", "task": "", "files": []}
        for text in ("continue", "Continue.", "ok, continue", "yes continue please", "go on", "Resume", "keep going", "carry on with the tests"):
            self.assertEqual(turn.resume_context({"paused": dict(paused)}, text, str(self.tmp))["mode"], "resume", text)
        for text in ("yes", "why did it stop?", "continuous integration is red", "fix the login bug instead"):
            self.assertEqual(turn.resume_context({"paused": dict(paused)}, text, str(self.tmp))["mode"], "pointer", text)
        cwd = str(self.tmp / "app")
        checkpoint = self._pause("s-point", cwd)
        told = self._prompt("s-point", "why did the parser tests fail?", cwd)
        self.assertIn(str(checkpoint), told["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("n of m verified", told["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("systemMessage", told)
        self.assertNotIn(str(checkpoint), json.dumps(self._prompt("s-point", "continue", cwd)), "the first prompt after the pause uses it up")

    def test_resumes_and_skipped_pauses_are_logged(self):
        cwd = str(self.tmp / "app")
        self._pause("s-log", cwd).write_text("- [ ] tests\n")
        self._prompt("s-log", "continue", cwd)
        self._pause("s-skip", cwd)
        self._prompt("s-skip", "why did it stop?", cwd)
        logged = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        follow_ups = [{k: e.get(k) for k in ("session_id", "mode", "checklist", "paused_session", "steps")} for e in logged if e.get("event") == "turn_resume"]
        self.assertEqual(follow_ups, [
            {"session_id": "s-log", "mode": "resume", "checklist": True, "paused_session": "s-log", "steps": constants.TURN_STOP_STEPS},
            {"session_id": "s-skip", "mode": "pointer", "checklist": False, "paused_session": "s-skip", "steps": constants.TURN_STOP_STEPS}])

    def test_a_new_session_in_the_project_offers_the_paused_work_and_its_continue_resumes_it(self):
        cwd = str(self.tmp / "app")
        checkpoint = self._pause("s-old", cwd)
        checkpoint.write_text("- [ ] tests for the parser\n")

        def start(sid, source, where=cwd):
            return session.handle_session_start({"hook_event_name": "SessionStart", "session_id": sid, "source": source, "cwd": where}) or {}

        self.assertNotIn("systemMessage", start("s-old", "resume"), "the paused session resumes from its own state")
        self.assertNotIn("systemMessage", start("s-elsewhere", "startup", str(self.tmp / "other")), "only the same project is offered it")
        offered = start("s-new", "clear")
        self.assertIn(str(checkpoint), offered["systemMessage"])
        self.assertIn(str(checkpoint), offered["hookSpecificOutput"]["additionalContext"])
        self.assertIn(f"Kiasi: resuming from {checkpoint}", self._prompt("s-new", "Continue please", cwd)["systemMessage"])
        self.assertNotIn("systemMessage", start("s-later", "startup"), "a resumed pause is not offered again")

    def test_a_resume_without_a_checklist_starts_from_the_task_and_the_edited_files(self):
        transcript = self.tmp / "s-task.jsonl"
        transcript.write_text("".join(json.dumps(entry) + "\n" for entry in (
            {"type": "user", "message": {"content": "Build the CSV parser for the import screen and add tests for it"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/app/parser.py"}}]}},
        )))
        cwd = str(self.tmp / "app")
        self._pause("s-task", cwd, transcript)
        resumed = self._prompt("s-task", "continue", cwd)
        context = resumed["hookSpecificOutput"]["additionalContext"]
        for part in ("no checklist was written", "Build the CSV parser for the import screen", "/app/parser.py", "n of m verified"):
            self.assertIn(part, context)
        self.assertIn("no checklist was written", resumed["systemMessage"])

    def test_a_paused_subagent_leaves_nothing_to_resume(self):
        subagent = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-subp", "cwd": str(self.tmp / "app"),
                    "transcript_path": str(self.tmp / "s-subp.jsonl"), "agent_id": "a7c2e19b04d5f3a68"}
        for _ in range(constants.SUBAGENT_STEP_LIMIT):
            turn.turn_guard(subagent, 1000)
        state = events.load_session("s-subp")
        self.assertTrue(any(t.get("stopped") for t in state["turns"].values()))
        self.assertNotIn("paused", state, "a subagent hands its checklist to its caller, which goes on")
        self.assertEqual(list(constants.NOTES_DIR.glob("*.paused.json")), [])

    def test_the_warning_comes_ten_calls_before_the_pause_and_counts_what_is_left(self):
        def said(sid, tokens, steps):
            payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": sid, "transcript_path": str(self.tmp / f"{sid}.jsonl")}
            return [(n, r["systemMessage"]) for n, r in enumerate((turn.turn_guard(payload, tokens) for _ in range(steps)), 1) if r]

        narrow = said("s-left", 1000, constants.TURN_STOP_STEPS)
        self.assertEqual([n for n, _ in narrow], [constants.TURN_STOP_STEPS - constants.TURN_WARN_MARGIN, constants.TURN_STOP_STEPS])
        self.assertIn(f"about {constants.TURN_WARN_MARGIN} tool calls left", narrow[0][1])
        # A wide context runs out of tokens first: at 200k a call it warns at 6.4M and pauses at 8M, 8 calls later.
        wide = said("s-wide", 200_000, constants.TURN_STOP_TOKENS // 200_000)
        self.assertEqual([n for n, _ in wide], [constants.turn_warn_tokens() // 200_000, constants.TURN_STOP_TOKENS // 200_000])
        self.assertIn(f"about {(constants.TURN_STOP_TOKENS - constants.turn_warn_tokens()) // 200_000} tool calls left", wide[0][1])
        self.addCleanup(setattr, constants, "TURN_WARN_STEPS", constants.TURN_WARN_STEPS)
        (self.tmp / constants.PROJECT_FILE_NAME).write_text(json.dumps({"turn_warn_steps": 25}))
        constants.apply_project(str(self.tmp))
        self.assertEqual([n for n, _ in said("s-set", 1000, constants.TURN_STOP_STEPS)], [25, constants.TURN_STOP_STEPS],
                         "turn_warn_steps in .kiasi.json still sets the warning")

    def test_subagent_calls_have_their_own_turn(self):
        main = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-agents", "transcript_path": str(self.tmp / "s-agents.jsonl")}
        results = [turn.turn_guard(main, 1000) for _ in range(constants.TURN_STOP_STEPS)]
        self.assertIn("kiasi paused this turn", results[-1]["hookSpecificOutput"]["additionalContext"])
        subagent = {**main, "agent_id": "a1fd597a875bd1f14"}
        handed_off = [turn.turn_guard(subagent, 1000) for _ in range(constants.TURN_REMIND_STEPS)]
        self.assertFalse([r for r in handed_off if r], "the subagent a paused turn hands its checklist to must be able to work")
        self.assertIsNone(turn.paused_call({**subagent, "hook_event_name": "PreToolUse"}))
        self.assertIsNotNone(turn.paused_call({**main, "hook_event_name": "PreToolUse"}))
        turns = events.load_session("s-agents")["turns"]
        self.assertEqual(turns["s-agents"]["steps"], constants.TURN_STOP_STEPS)
        self.assertEqual(turns["a1fd597a875bd1f14"]["steps"], constants.TURN_REMIND_STEPS)
        prompt.handle_prompt({"prompt": "the next task after the hand-off", "session_id": "s-agents", "transcript_path": main["transcript_path"]})
        self.assertNotIn("a1fd597a875bd1f14", events.load_session("s-agents")["turns"])

    def test_subagent_calls_reread_their_own_context(self):
        main_path = self.tmp / "s-ctx.jsonl"
        agent_path = self.tmp / "s-ctx" / "subagents" / "agent-a4bb4aca3bed22433.jsonl"
        agent_path.parent.mkdir(parents=True)
        for path, tokens in ((main_path, 150_000), (agent_path, 20_000)):
            path.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": tokens - 10}}}) + "\n")
        payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "make"}, "session_id": "s-ctx",
                   "transcript_path": str(main_path), "agent_id": "a4bb4aca3bed22433"}
        turn.handle_tool_failure(payload)
        self.assertEqual(events.load_session("s-ctx")["turns"]["a4bb4aca3bed22433"]["reread"], 20_000)

    def test_a_subagent_stops_at_its_own_budget(self):
        main = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-sub", "transcript_path": str(self.tmp / "s-sub.jsonl")}
        subagent = {**main, "agent_id": "a7c2e19b04d5f3a68"}
        results = [turn.turn_guard(subagent, 1000) for _ in range(constants.SUBAGENT_STEP_LIMIT)]
        said = [(n, r["hookSpecificOutput"]["additionalContext"]) for n, r in enumerate(results, 1) if r]
        self.assertEqual([n for n, _ in said], [constants.warn_steps(constants.SUBAGENT_STEP_LIMIT), constants.SUBAGENT_STEP_LIMIT])
        self.assertIn(f"pauses at {constants.SUBAGENT_STEP_LIMIT} tool calls", said[0][1])
        self.assertIn("kiasi paused this subagent", said[1][1])
        self.assertIn("reply to the caller", said[1][1])
        self.assertNotIn("Agent call", said[1][1], "a subagent cannot start another subagent")
        main_results = [turn.turn_guard(main, 1000) for _ in range(constants.turn_warn_steps())]
        self.assertFalse([r for r in main_results if r and "kiasi paused" in r["hookSpecificOutput"]["additionalContext"]],
                         "the main turn keeps its own budget")
        logged = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        self.assertEqual([e.get("subagent") for e in logged if e["event"] in ("turn_warn", "turn_stop")], [True, True, False])

    def test_checkpoint_path_never_names_an_existing_file(self):
        constants.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        record = {"index": 7}
        names = []
        for _ in range(3):
            path = turn.checkpoint_path("0123abcd-transcript", record)
            names.append(path.name)
            path.write_text("- [ ] left\n")
        self.assertEqual(names, ["0123abcd-7.md", "0123abcd-7-2.md", "0123abcd-7-3.md"])

    def test_stop_names_a_new_file_once_the_warned_checklist_exists(self):
        constants.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "sess-checklist",
                   "transcript_path": str(self.tmp / "checklist.jsonl")}
        warning = stop = ""
        for _ in range(constants.TURN_STOP_STEPS):
            result = turn.turn_guard(payload, 1000) or {}
            context = result.get("hookSpecificOutput", {}).get("additionalContext", "")
            if context and not warning:
                warning = context
                (constants.CHECKPOINT_DIR / "checklis-0.md").write_text("- [ ] left\n")
            elif context:
                stop = context
        self.assertIn(str(constants.CHECKPOINT_DIR / "checklis-0.md"), warning)
        self.assertIn(str(constants.CHECKPOINT_DIR / "checklis-0-2.md"), stop)

    def settings(self, **values):
        for name, value in values.items():
            self.addCleanup(setattr, constants, name, getattr(constants, name))
            setattr(constants, name, value)

    def test_warn_mode_reports_the_budget_but_refuses_nothing(self):
        self.settings(TURN_BUDGET_MODE="warn")
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-warn", "cwd": str(self.tmp),
                   "transcript_path": str(self.tmp / "s-warn.jsonl")}
        results = [turn.turn_guard(payload, 1000) for _ in range(constants.TURN_STOP_STEPS + 5)]
        said = [(n, result) for n, result in enumerate(results, 1) if result]
        self.assertEqual([n for n, _ in said], [constants.turn_warn_steps(), constants.TURN_STOP_STEPS], "once at the warning, once at the budget")
        (_, warning), (_, over) = said
        self.assertIn("which kiasi reports but does not enforce", warning["hookSpecificOutput"]["additionalContext"])
        self.assertIn("before this turn reaches its budget", warning["systemMessage"])
        self.assertIn("warn mode, so no call is refused", over["hookSpecificOutput"]["additionalContext"])
        self.assertIn(f"reached its budget at {constants.TURN_STOP_STEPS} tool calls", over["systemMessage"])
        self.assertNotIn("terminalSequence", over, "no desktop notification when nothing stops")
        self.assertIsNone(turn.paused_call({**payload, "hook_event_name": "PreToolUse"}))
        state = events.load_session("s-warn")
        self.assertNotIn("paused", state, "nothing is saved to resume")
        [counted] = state["turns"].values()
        self.assertFalse(counted["stopped"])
        self.assertEqual(state["turn_budget"]["mode"], "warn")

    def test_off_mode_only_counts_calls(self):
        self.settings(TURN_BUDGET_MODE="off")
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-off", "cwd": str(self.tmp),
                   "transcript_path": str(self.tmp / "s-off.jsonl")}
        results = [turn.turn_guard(payload, 1000) for _ in range(constants.TURN_STOP_STEPS + 5)]
        self.assertEqual([result for result in results if result], [])
        self.assertIsNone(turn.paused_call({**payload, "hook_event_name": "PreToolUse"}))
        state = events.load_session("s-off")
        [counted] = state["turns"].values()
        self.assertEqual(counted["steps"], constants.TURN_STOP_STEPS + 5, "calls are still counted for the reports")
        import statusline
        self.assertIsNone(statusline.turn_part(payload, state), "the status line shows no budget")
        state["turn_budget"]["mode"] = "pause"
        self.assertIn(f"{constants.TURN_STOP_STEPS + 5}/{constants.TURN_STOP_STEPS}", statusline.turn_part(payload, state))

    def test_a_turn_paused_before_the_mode_changed_is_let_go(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-switch", "cwd": str(self.tmp),
                   "transcript_path": str(self.tmp / "s-switch.jsonl")}
        for _ in range(constants.TURN_STOP_STEPS):
            turn.turn_guard(payload, 1000)
        call = {**payload, "hook_event_name": "PreToolUse"}
        self.assertEqual(turn.paused_call(call)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.settings(TURN_BUDGET_MODE="warn")
        self.assertIsNone(turn.paused_call(call), "only pause mode refuses calls")

    def test_the_session_rules_follow_the_budget_settings(self):
        self.settings(TURN_BUDGET_MODE="pause", TURN_STOP_STEPS=60, TURN_STOP_TOKENS=8_000_000, TURN_WARN_STEPS=None, SUBAGENT_STEP_LIMIT=40)
        self.assertEqual(session.rules_text(), constants.RULES_FILE.read_text().strip(), "the defaults render rules.md as written")
        self.settings(TURN_STOP_STEPS=80, TURN_STOP_TOKENS=2_500_000, SUBAGENT_STEP_LIMIT=25)
        text = session.rules_text()
        self.assertIn("budget of 80 tool calls or 2.5M re-read tokens. At the warning, about 10 calls before the pause", text)
        self.assertIn("own budget of 25 tool calls", text)
        self.settings(TURN_BUDGET_MODE="warn")
        text = session.rules_text()
        self.assertIn("which kiasi reports but does not enforce", text)
        self.assertNotIn("every call except Write and Agent is refused", text)
        self.settings(TURN_BUDGET_MODE="off")
        lines = session.rules_text().splitlines()
        self.assertEqual([line for line in lines if "Every turn has a budget" in line], [])
        self.assertIn("- Scope review subagents to the diff, never the whole repo.", lines)

    def test_a_subagent_brief_states_its_budget_unless_the_budget_is_off(self):
        brief = {"session_id": "s-brief", "tool_input": {"prompt": "Fix the failing test in a.py", "subagent_type": "general-purpose"}}
        stated = reads.handle_agent(brief)["hookSpecificOutput"]["updatedInput"]["prompt"]
        self.assertIn(f"Kiasi budget: finish within {constants.SUBAGENT_STEP_LIMIT} tool calls. Batch shell commands", stated)
        self.settings(TURN_BUDGET_MODE="off")
        plain = reads.handle_agent(brief)["hookSpecificOutput"]["updatedInput"]["prompt"]
        self.assertNotIn("Kiasi budget", plain)
        self.assertTrue(plain.endswith(constants.SUBAGENT_BRIEF_SUFFIX))


class TestLoopCheck(KiasiTestCase):
    def fail(self, command):
        return turn.handle_tool_failure({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "session_id": "s-loop",
                                          "transcript_path": "/tmp/loop.jsonl", "tool_input": {"command": command}, "error": "exit 1"})

    def test_nudges_once_at_third_identical_failure(self):
        self.assertIsNone(self.fail("pnpm build"))
        self.assertIsNone(self.fail("pnpm build"))
        third = self.fail("pnpm build")
        self.assertIn("failed 3 times", third["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(third["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")
        self.assertIsNone(self.fail("pnpm build"))

    def test_nudges_on_variations_of_one_command(self):
        results = [self.fail(f"pnpm build --filter app{i}") for i in range(5)]
        self.assertTrue(all(r is None for r in results[:4]))
        self.assertIn("with different arguments", results[4]["hookSpecificOutput"]["additionalContext"])

    def test_failures_count_toward_turn_budget_and_reset_per_prompt(self):
        for _ in range(3):
            self.fail("make")
        state = events.load_session("s-loop")
        self.assertEqual(state["turns"]["loop"]["steps"], 3)
        prompt.handle_prompt({"prompt": "a new task for the loop test", "session_id": "s-loop", "transcript_path": "/tmp/loop.jsonl"})
        self.assertIsNone(self.fail("make"))

    def test_interrupts_are_ignored(self):
        payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "session_id": "s-loop", "is_interrupt": True, "tool_input": {"command": "x"}}
        self.assertIsNone(turn.handle_tool_failure(payload))


class TestSessionLock(KiasiTestCase):
    SID = "0c1d2e3f-0000-4000-8000-000000000001"

    def test_parallel_hooks_keep_every_count(self):
        # Each hook process pauses between loading the session and saving it, as a slow hook does:
        # without the lock all six load the same state and every save but the last is lost.
        script = ("import sys, time; sys.path.insert(0, sys.argv[1]); from core import turn; load = turn.load_session; "
                  "turn.load_session = lambda sid: (load(sid), time.sleep(0.2))[0]; import kiasi; kiasi.main()")
        payload = self.tmp / "payload.json"
        payload.write_text(json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": self.SID,
                                       "transcript_path": str(self.tmp / "parallel.jsonl"), "tool_input": {"command": "true"}}))
        env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(self.tmp)}
        hooks = []
        for _ in range(6):
            with open(payload) as stdin:
                hooks.append(subprocess.Popen([sys.executable, "-c", script, str(SCRIPTS)], stdin=stdin, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE, env=env, text=True))
        self.assertEqual([hook.communicate(timeout=60)[1] for hook in hooks], [""] * 6)
        self.assertEqual([t["steps"] for t in events.load_session(self.SID)["turns"].values()], [6])

    def test_a_busy_lock_is_given_up_instead_of_hanging_the_hook(self):
        self.addCleanup(setattr, constants, "SESSION_LOCK_WAIT_SECONDS", constants.SESSION_LOCK_WAIT_SECONDS)
        constants.SESSION_LOCK_WAIT_SECONDS = 0.2
        with events.session_lock(self.SID) as first:
            started = time.monotonic()
            with events.session_lock(self.SID) as second:
                waited = time.monotonic() - started
        with events.session_lock(self.SID) as third:
            pass
        self.assertEqual((first, second, third), (True, False, True))
        self.assertLess(waited, 2)
