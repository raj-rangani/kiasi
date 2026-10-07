

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from helpers import SCRIPTS, KiasiTestCase
from core import constants, taskfile  # noqa: E402
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
        self.assertIn(f"about {constants.TURN_WARN_MARGIN} steps left", warning["systemMessage"])
        self.assertNotIn("decision", paused, "a PostToolUse block shows the developer a hook error and stops nothing")
        checkpoint = events.load_session("sess-turn")["turns"]["missing-transcript"]["checkpoint"]
        self.assertIn(checkpoint, paused["hookSpecificOutput"]["additionalContext"])
        notice = paused["systemMessage"]
        for part in (f"Kiasi paused this turn at {constants.TURN_STOP_STEPS} steps", checkpoint, 'Reply "continue"', "/clear", "100k tokens"):
            self.assertIn(part, notice)
        self.assertNotIn("exhausted", notice + paused["hookSpecificOutput"]["additionalContext"])
        # Claude Code passes on only OSC 0/1/2/9/99/777 and BEL, and an OSC 9 body may not start with a digit.
        self.assertEqual(paused["terminalSequence"], f"\x1b]9;Kiasi paused this turn at {constants.TURN_STOP_STEPS} steps\x07\x07")

    def test_a_paused_turn_refuses_calls_then_ends_the_turn(self):
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-pause", "transcript_path": str(self.tmp / "s-pause.jsonl")}
        pre = {**post, "hook_event_name": "PreToolUse", "tool_name": "Edit"}
        for _ in range(constants.TURN_STOP_STEPS - 1):
            turn.turn_guard(post, 1000)
        self.assertIsNone(turn.handle_pre_tool_use(pre), "nothing is refused before the pause")
        turn.turn_guard(post, 1000)
        for tool in ("Write", "Agent", "TodoWrite"):
            self.assertIsNone(turn.paused_call({**pre, "tool_name": tool}), f"{tool} stays allowed for the checklist and the hand-off")
        refused = []
        for response in range(constants.TURN_DENY_BACKSTOP):
            with mock.patch("core.turn.time.time", return_value=1000.0 + response * 10):
                refused.append(turn.handle_pre_tool_use(pre))
        self.assertEqual({r["hookSpecificOutput"]["permissionDecision"] for r in refused}, {"deny"})
        reason = refused[0]["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("so this call was not run", reason)
        self.assertIn(events.load_session("s-pause")["turns"]["s-pause"]["checkpoint"], reason)
        self.assertFalse([r for r in refused[:-1] if "continue" in r], "the first refusals leave Claude room to write the checklist")
        self.assertIs(refused[-1]["continue"], False)
        self.assertIn("Kiasi paused this turn", refused[-1]["stopReason"])
        self.assertIn("Kiasi ended this turn: Claude kept calling tools after the pause", refused[-1]["systemMessage"])
        self.assertNotIn("systemMessage", refused[0], "a refusal before the backstop says nothing more to the developer")
        prompt.handle_prompt({"prompt": "continue", "session_id": "s-pause", "transcript_path": post["transcript_path"]})
        self.assertIsNone(turn.handle_pre_tool_use(pre), "the next prompt starts a fresh budget")

    def pause(self, session, transcript_entries=None):
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": session, "transcript_path": str(self.tmp / f"{session}.jsonl")}
        if transcript_entries is not None:
            Path(post["transcript_path"]).write_text("".join(json.dumps(e) + "\n" for e in transcript_entries))
        for _ in range(constants.TURN_STOP_STEPS):
            turn.turn_guard(post, 1000)
        return {**post, "hook_event_name": "PreToolUse", "tool_name": "Edit"}

    def response(self, message_id, *call_ids):
        return {"type": "assistant", "message": {"id": message_id, "content": [{"type": "tool_use", "id": c, "name": "Edit", "input": {}} for c in call_ids]}}

    def test_parallel_refusals_of_one_response_count_once(self):
        pre = self.pause("s-par", [self.response("msg_1", "t1", "t2", "t3")])
        refused = [turn.paused_call({**pre, "tool_use_id": f"t{n}"}) for n in (1, 2, 3)]
        self.assertFalse([r for r in refused if "continue" in r], "three calls of one response are one refusal")
        self.assertEqual(events.load_session("s-par")["turns"]["s-par"]["denied"], 1)
        Path(pre["transcript_path"]).write_text(json.dumps(self.response("msg_2", "t4")) + "\n")
        turn.paused_call({**pre, "tool_use_id": "t4"})
        self.assertEqual(events.load_session("s-par")["turns"]["s-par"]["denied"], 2, "a later response counts separately")

    def test_refusals_without_a_transcript_count_by_time(self):
        pre = self.pause("s-time")
        with mock.patch("core.turn.time.time", return_value=1000.0):
            refused = [turn.paused_call({**pre, "tool_use_id": f"t{n}"}) for n in (1, 2, 3)]
        self.assertFalse([r for r in refused if "continue" in r])
        self.assertEqual(events.load_session("s-time")["turns"]["s-time"]["denied"], 1)
        with mock.patch("core.turn.time.time", return_value=1000.0 + constants.REFUSAL_RESPONSE_SECONDS + 1):
            turn.paused_call({**pre, "tool_use_id": "t4"})
        self.assertEqual(events.load_session("s-time")["turns"]["s-time"]["denied"], 2)

    def test_a_fully_refused_batch_does_not_count_again(self):
        pre = self.pause("s-batch", [self.response("msg_1", "t1", "t2")])
        for n in (1, 2):
            turn.paused_call({**pre, "tool_use_id": f"t{n}"})
        batch = {"hook_event_name": "PostToolBatch", "session_id": "s-batch", "transcript_path": pre["transcript_path"],
                 "tool_calls": [{"tool_use_id": "t1", "tool_name": "Edit"}, {"tool_use_id": "t2", "tool_name": "Edit"}]}
        self.assertIsNone(turn.handle_tool_batch(batch))
        self.assertEqual(events.load_session("s-batch")["turns"]["s-batch"]["denied"], 1)

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
                   "transcript_path": str(self.tmp / "s-refused.jsonl"), "error": "kiasi paused this turn at 60 steps, so this call was not run."}
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
        self.assertIn(f"Paused by Kiasi at {constants.TURN_STOP_STEPS} steps; the rest is in the task file {checkpoint('s-remind')}",
                      reminder["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(session.handle_stop(stop), "the reminder is sent once")
        told = paused("s-told")
        self.assertIsNone(session.handle_stop({**told, "last_assistant_message": f"Paused by Kiasi; the rest is in the task file {checkpoint('s-told')}."}))
        self.assertIsNone(session.handle_stop({**paused("s-active"), "stop_hook_active": True}), "another Stop hook already made Claude reply")
        ended = paused("s-ended")
        for response in range(constants.TURN_DENY_BACKSTOP):
            with mock.patch("core.turn.time.time", return_value=1000.0 + response * 10):
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
        for part in ("has no checklist yet", "Build the CSV parser for the import screen", "/app/parser.py", "n of m verified"):
            self.assertIn(part, context)
        self.assertIn("has no checklist yet", resumed["systemMessage"])

    def test_the_checklist_goes_in_the_project_in_a_folder_git_ignores(self):
        # Claude Code refuses Claude's writes under ~/.claude, Kiasi's data folder included, as edits to a sensitive file.
        project = self.tmp / "project"
        project.mkdir()
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-proj", "cwd": str(project),
                "transcript_path": str(self.tmp / "s-proj.jsonl")}
        for _ in range(constants.turn_warn_steps() - 1):
            turn.turn_guard(post, 1000)
        self.assertFalse((project / ".kiasi").exists(), "a turn far from its budget leaves the project alone")
        results = [turn.turn_guard(post, 1000) for _ in range(constants.TURN_STOP_STEPS - constants.turn_warn_steps() + 1)]
        checkpoint = Path(events.load_session("s-proj")["turns"]["s-proj"]["checkpoint"])
        self.assertEqual(checkpoint.parent, project / ".kiasi" / "checkpoints")
        self.assertTrue(checkpoint.parent.is_dir())
        self.assertEqual((project / ".kiasi" / ".gitignore").read_text(), "*\n")
        self.assertIn(str(checkpoint), results[0]["hookSpecificOutput"]["additionalContext"])
        self.assertIn(str(checkpoint), results[-1]["systemMessage"])
        self.assertEqual(json.loads(turn.paused_file(str(project), "s-proj").read_text())["checkpoint"], str(checkpoint))
        logged = {e["event"]: e.get("checkpoint") for e in map(json.loads, constants.EVENT_LOG.read_text().splitlines()) if e.get("session_id") == "s-proj"}
        self.assertEqual((logged["turn_warn"], logged["turn_stop"]), (str(checkpoint), str(checkpoint)), "cleanup finds project checklist folders through these events")

    def test_without_a_writable_project_folder_the_checklist_stays_in_the_data_folder(self):
        for cwd in (None, "", str(self.tmp / "missing")):
            self.assertEqual(turn.checkpoint_path("abcdef12", {"index": 3}, cwd), constants.TEMP_CHECKLIST_DIR / "abcdef12-3.md")

    def test_a_pause_in_a_resumed_turn_keeps_the_resumed_task_and_files(self):
        first = self.tmp / "s-first.jsonl"
        first.write_text("".join(json.dumps(entry) + "\n" for entry in (
            {"type": "user", "message": {"content": "Build the CSV parser for the import screen and add tests for it"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/app/parser.py"}}]}},
        )))
        cwd = str(self.tmp / "app")
        self._pause("s-first", cwd, first)
        session.handle_session_start({"hook_event_name": "SessionStart", "session_id": "s-second", "source": "clear", "cwd": cwd})
        self._prompt("s-second", "continue", cwd)
        # The resumed turn's only prompt is "continue", and it pauses again before Claude writes a checklist.
        second = self.tmp / "s-second.jsonl"
        second.write_text("".join(json.dumps(entry) + "\n" for entry in (
            {"type": "user", "message": {"content": "continue"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/app/test_parser.py"}}]}},
        )))
        self._pause("s-second", cwd, second)
        paused = events.load_session("s-second")["paused"]
        self.assertEqual(paused["task"], "Build the CSV parser for the import screen and add tests for it")
        self.assertEqual(paused["files"], ["/app/parser.py", "/app/test_parser.py"])
        self.assertIn("Build the CSV parser for the import screen", self._prompt("s-second", "continue", cwd)["hookSpecificOutput"]["additionalContext"])
        self._prompt("s-second", "Now add a JSON exporter to the import screen, with its own tests", cwd)
        self.assertNotIn("resumed", events.load_session("s-second"), "a new task is not mixed with the work resumed before it")

    def test_a_paused_subagent_leaves_nothing_to_resume(self):
        subagent = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-subp", "cwd": str(self.tmp / "app"),
                    "transcript_path": str(self.tmp / "s-subp.jsonl"), "agent_id": "a7c2e19b04d5f3a68"}
        for _ in range(constants.SUBAGENT_STEP_LIMIT):
            turn.turn_guard(subagent, 1000)
        state = events.load_session("s-subp")
        self.assertTrue(any(t.get("stopped") for t in state["turns"].values()))
        self.assertNotIn("paused", state, "a subagent hands its checklist to its caller, which goes on")
        self.assertEqual(list(constants.NOTES_DIR.glob("*.paused*.json")), [])

    def test_the_warning_comes_ten_calls_before_the_pause_and_counts_what_is_left(self):
        def said(sid, tokens, steps):
            payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": sid, "transcript_path": str(self.tmp / f"{sid}.jsonl")}
            return [(n, r["systemMessage"]) for n, r in enumerate((turn.turn_guard(payload, tokens) for _ in range(steps)), 1) if r]

        narrow = said("s-left", 1000, constants.TURN_STOP_STEPS)
        self.assertEqual([n for n, _ in narrow], [constants.TURN_STOP_STEPS - constants.TURN_WARN_MARGIN, constants.TURN_STOP_STEPS])
        self.assertIn(f"about {constants.TURN_WARN_MARGIN} steps left", narrow[0][1])
        # A wide context runs out of tokens first: at 200k a call it warns at 6.4M and pauses at 8M, 8 calls later.
        wide = said("s-wide", 200_000, constants.TURN_STOP_TOKENS // 200_000)
        self.assertEqual([n for n, _ in wide], [constants.turn_warn_tokens() // 200_000, constants.TURN_STOP_TOKENS // 200_000])
        self.assertIn(f"about {(constants.TURN_STOP_TOKENS - constants.turn_warn_tokens()) // 200_000} steps left", wide[0][1])
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
        turns = events.load_session("s-agents")["turns"]
        self.assertEqual(turns["s-agents"]["steps"], 0, "the next prompt starts a fresh turn")
        self.assertEqual(turns["a1fd597a875bd1f14"]["steps"], constants.TURN_REMIND_STEPS, "a subagent still running keeps its count")

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
        self.assertIn(f"pauses at {constants.SUBAGENT_STEP_LIMIT} steps", said[0][1])
        self.assertIn("kiasi paused this subagent", said[1][1])
        self.assertIn("reply to the caller", said[1][1])
        self.assertNotIn("Agent call", said[1][1], "a subagent cannot start another subagent")
        main_results = [turn.turn_guard(main, 1000) for _ in range(constants.turn_warn_steps())]
        self.assertFalse([r for r in main_results if r and "kiasi paused" in r["hookSpecificOutput"]["additionalContext"]],
                         "the main turn keeps its own budget")
        logged = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        self.assertEqual([e.get("subagent") for e in logged if e["event"] in ("turn_warn", "turn_stop")], [True, True, False])

    def test_checkpoint_path_never_names_an_existing_file(self):
        constants.TEMP_CHECKLIST_DIR.mkdir(parents=True, exist_ok=True)
        record = {"index": 7}
        names = []
        for _ in range(3):
            path = turn.checkpoint_path("0123abcd-transcript", record)
            names.append(path.name)
            path.write_text("- [ ] left\n")
        self.assertEqual(names, ["0123abcd-7.md", "0123abcd-7-2.md", "0123abcd-7-3.md"])

    def test_stop_names_the_warned_checklist_and_asks_for_it_to_be_updated(self):
        constants.TEMP_CHECKLIST_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "sess-checklist",
                   "transcript_path": str(self.tmp / "checklist.jsonl")}
        warning = stop = ""
        for _ in range(constants.TURN_STOP_STEPS):
            result = turn.turn_guard(payload, 1000) or {}
            context = result.get("hookSpecificOutput", {}).get("additionalContext", "")
            if context and not warning:
                warning = context
                (taskfile.path_for(None, "sess-checklist")).write_text("- [ ] left\n")
            elif context:
                stop = context
        self.assertIn(str(taskfile.path_for(None, "sess-checklist")), warning)
        self.assertIn(str(taskfile.path_for(None, "sess-checklist")), stop)
        self.assertIn("up to date", stop)

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
        self.assertIn(f"reached its budget at {constants.TURN_STOP_STEPS} steps", over["systemMessage"])
        self.assertNotIn("terminalSequence", over, "no desktop notification when nothing stops")
        self.assertIsNone(turn.paused_call({**payload, "hook_event_name": "PreToolUse"}))
        state = events.load_session("s-warn")
        self.assertNotIn("paused", state, "nothing is saved to resume")
        [counted] = state["turns"].values()
        self.assertFalse(counted["stopped"])
        self.assertEqual(state["turn_budget"]["mode"], "warn")
        logged = {e["event"]: e.get("checkpoint") for e in map(json.loads, constants.EVENT_LOG.read_text().splitlines()) if e.get("session_id") == "s-warn"}
        self.assertEqual({Path(logged[name]).parent for name in ("turn_warn", "turn_over")}, {self.tmp / ".kiasi" / "checkpoints"})

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
        budget = [line for line in constants.RULES_FILE.read_text().strip().splitlines() if line.startswith(("- Every turn has a budget", "- Each subagent has its own budget"))]
        self.assertEqual(session.rules_text().splitlines()[1:3], budget, "the defaults render the budget lines of rules.md as written")
        self.settings(TURN_STOP_STEPS=80, TURN_STOP_TOKENS=2_500_000, SUBAGENT_STEP_LIMIT=25)
        text = session.rules_text()
        self.assertIn("budget of 80 steps (the tool calls of one response are one step, so parallel calls count once) or 2.5M re-read tokens. At the warning, about 10 steps before the pause", text)
        self.assertIn("own budget of 25 steps", text)
        self.settings(TURN_BUDGET_MODE="warn")
        text = session.rules_text()
        self.assertIn("which kiasi reports but does not enforce", text)
        self.assertNotIn("every call except Write, Agent and the question kiasi gives is refused", text)
        self.settings(TURN_BUDGET_MODE="off")
        lines = session.rules_text().splitlines()
        self.assertEqual([line for line in lines if "Every turn has a budget" in line], [])
        self.assertIn("- Scope review subagents to the diff, never the whole repo.", lines)

    def test_the_session_start_block_is_short_and_points_at_the_full_rules(self):
        lines = session.rules_text().splitlines()
        self.assertLessEqual(len(lines), 12)
        self.assertIn("Load /kiasi:rules for the full rules.", lines)
        self.assertTrue((constants.PLUGIN_ROOT / "skills" / "rules" / "SKILL.md").exists())

    def test_a_tool_call_records_the_context_numbers(self):
        path = self.tmp / "t.jsonl"
        rows = [{"type": "assistant", "message": {"usage": {"input_tokens": 3, "cache_creation_input_tokens": 40000, "cache_read_input_tokens": 5000}}},
                {"type": "assistant", "message": {"usage": {"input_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 90000}}}]
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        state = {}
        turn.note_context({"session_id": "s-ctx", "transcript_path": str(path)}, state, 90105)
        self.assertEqual((state["context_floor"], state["context_tokens"]), (45003, 90105))
        self.assertRegex(state["last_call_at"], r"^\d{4}-\d\d-\d\dT")
        turn.note_context({"session_id": "s-ctx", "transcript_path": str(path)}, state, 100000)
        self.assertEqual(state["context_floor"], 45003, "the floor is measured once")

    def test_the_guard_stores_the_context_keys_on_each_call(self):
        turn.turn_guard({"session_id": "s-keys", "tool_name": "Bash", "transcript_path": ""}, 70000)
        state = events.load_session("s-keys")
        self.assertEqual((state["context_tokens"], state["context_floor"]), (70000, constants.CONTEXT_FLOOR_DEFAULT))
        self.assertIn("last_call_at", state)

    def test_a_pause_writes_the_task_file_and_a_resume_names_it(self):
        state = {}
        turn_state = {"checkpoint": str(self.tmp / "cp.md"), "stopped": 3, "reread": 1}
        turn.record_pause({"session_id": "s-tf", "cwd": str(self.tmp), "transcript_path": ""}, turn_state, state)
        self.assertTrue(turn_state["taskfile"].endswith(".md"))
        self.assertIn(str(self.tmp / "cp.md"), open(turn_state["taskfile"]).read())
        self.assertEqual(state["paused"]["taskfile"], turn_state["taskfile"])

    def test_a_subagent_brief_states_its_budget_unless_the_budget_is_off(self):
        brief = {"session_id": "s-brief", "tool_input": {"prompt": "Fix the failing test in a.py", "subagent_type": "general-purpose"}}
        stated = reads.handle_agent(brief)["hookSpecificOutput"]["updatedInput"]["prompt"]
        self.assertIn(f"Kiasi budget: finish within {constants.SUBAGENT_STEP_LIMIT} steps; the tool calls of one response are one step, so parallel calls count once. Batch shell commands", stated)
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


class TestBatchCount(KiasiTestCase):
    SID = "s-batch"

    def setUp(self):
        super().setUp()
        path = self.tmp / "s-batch.jsonl"
        path.write_text(json.dumps({"type": "assistant", "message": {"id": "msg_1", "usage": {"input_tokens": 1000}, "content": []}}) + "\n")
        self.post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "true"}, "tool_response": {"stdout": "", "stderr": ""},
                     "session_id": self.SID, "transcript_path": str(path), "cwd": str(self.tmp)}

    def batch(self, *responses, **extra):
        calls = [{"tool_name": "Bash", "tool_input": {"command": "true"}, "tool_use_id": f"toolu_{index}", "tool_response": response}
                 for index, response in enumerate(responses)]
        return turn.handle_tool_batch({**self.post, "hook_event_name": "PostToolBatch", "tool_calls": calls, **extra})

    def count(self, key=SID):
        counted = events.load_session(self.SID)["turns"][key]
        return counted["steps"], counted["reread"]

    def test_the_first_batch_gives_the_extra_counts_back(self):
        response = Path(self.post["transcript_path"]).read_text()
        Path(self.post["transcript_path"]).write_text("")
        turn.handle_post_tool(self.post)
        Path(self.post["transcript_path"]).write_text(response)
        turn.handle_post_tool(self.post)
        turn.handle_post_tool(self.post)
        self.assertEqual(self.count(), (3, 2000), "no PostToolBatch seen yet: each call counts, the first before its response was written")
        self.assertIsNone(self.batch("ok", "ok", "ok"))
        self.assertEqual(self.count(), (1, 1000), "the next request re-reads the context once for the three calls")
        self.assertEqual(events.load_session(self.SID)["batch_hook"], ["turn"])

    def test_the_tool_calls_of_one_response_are_one_step(self):
        self.batch("ok")
        for _ in range(2):
            turn.handle_post_tool(self.post)
        turn.handle_tool_failure({**self.post, "hook_event_name": "PostToolUseFailure", "error": "exit 1"})
        self.assertEqual(events.load_session(self.SID).get("turns", {}).get(self.SID, {}).get("steps", 0), 0, "the batch counts, not its calls")
        self.batch("ok", "ok", "exit 1")
        self.assertEqual(self.count(), (1, 1000))
        self.batch("ok")
        self.assertEqual(self.count(), (2, 2000))

    def test_a_batch_of_refused_calls_is_not_a_step(self):
        self.batch("ok")
        self.batch("ok")
        state = events.load_session(self.SID)
        state["turns"][self.SID]["refused"] = ["toolu_0", "toolu_1"]
        events.save_session(self.SID, state)
        self.assertIsNone(self.batch("ok", "ok"))
        self.assertEqual(self.count(), (1, 1000), "calls refused at the pause, marked by id, are not a step")
        state["turns"][self.SID]["refused"] = ["toolu_0"]
        events.save_session(self.SID, state)
        self.batch("ok", "ok")
        self.assertEqual(self.count(), (2, 2000), "one call of the batch ran")
        self.batch("kiasi paused this turn, says a tool's own output")
        self.assertEqual(self.count(), (3, 3000), "text in an output does not make a call refused")

    def test_a_subagent_counts_call_by_call_until_its_own_batch_arrives(self):
        self.batch("ok")
        sub = {**self.post, "agent_id": "agent-x"}
        turn.handle_post_tool(sub)
        turn.handle_post_tool(sub)
        self.assertEqual(self.count("agent-x")[0], 2)
        self.batch("ok", "ok", agent_id="agent-x")
        self.assertEqual(self.count("agent-x")[0], 1)
        self.assertEqual(events.load_session(self.SID)["batch_hook"], ["turn", "subagent"])
        turn.handle_post_tool(sub)
        self.batch("ok", agent_id="agent-x")
        self.assertEqual(self.count("agent-x")[0], 2)
        self.assertNotIn(self.SID, events.load_session(self.SID)["turns"], "a subagent's batch is not a step of the turn")

    def test_the_pause_comes_from_the_batch(self):
        self.batch("ok")
        self.addCleanup(setattr, constants, "TURN_STOP_STEPS", constants.TURN_STOP_STEPS)
        constants.TURN_STOP_STEPS = 2
        self.batch("ok")
        paused = self.batch("ok", "ok")
        self.assertEqual(paused["hookSpecificOutput"]["hookEventName"], "PostToolBatch")
        self.assertIn("kiasi paused this turn: 2 steps", paused["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(events.load_session(self.SID)["turns"][self.SID]["stopped"], 2)


class TestPauseQuestion(KiasiTestCase):
    SID = "s-ask"
    QUESTION = "Kiasi paused this turn at 3 steps (0.0M tokens re-read). How should the rest go on?"

    def setUp(self):
        super().setUp()
        for name, value in (("PAUSE_QUESTION", "always"), ("TURN_STOP_STEPS", 3), ("SUBAGENT_STEP_LIMIT", 3)):
            self.addCleanup(setattr, constants, name, getattr(constants, name))
            setattr(constants, name, value)
        self.cwd = str(self.tmp / "app")
        self.post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": self.SID, "cwd": self.cwd,
                     "transcript_path": str(self.tmp / f"{self.SID}.jsonl")}

    def pause(self, **extra):
        for _ in range(constants.TURN_STOP_STEPS):
            result = turn.turn_guard({**self.post, **extra}, 1000)
            if result and "paused this" in result["hookSpecificOutput"]["additionalContext"]:
                return result

    def asked(self, reply, header=constants.PAUSE_QUESTION_HEADER, labels=tuple(constants.PAUSE_CHOICES.values())):
        questions = [{"question": self.QUESTION, "header": header, "multiSelect": False,
                      "options": [{"label": label, "description": "what it does"} for label in labels]}]
        response = reply if reply.startswith("Your questions") else {"questions": questions, "answers": {self.QUESTION: reply}, "annotations": {}}
        return {**self.post, "tool_name": "AskUserQuestion", "tool_input": {"questions": questions}, "tool_response": response}

    def answer(self, reply, **kwargs):
        return turn.handle_post_tool(self.asked(reply, **kwargs)) or {}

    def current(self):
        return events.load_session(self.SID)["turns"][self.SID]

    def refused(self):
        return turn.paused_call({**self.post, "hook_event_name": "PreToolUse"}) is not None

    def test_the_pause_asks_claude_to_put_the_question(self):
        paused = self.pause()
        context = paused["hookSpecificOutput"]["additionalContext"]
        for part in ("make one AskUserQuestion call", f'the header "{constants.PAUSE_QUESTION_HEADER}"', *constants.PAUSE_CHOICES.values(),
                     self.current()["checkpoint"], "fresh budget of 3 steps", "If that call cannot be made or is refused", "Paused by Kiasi at 3 steps"):
            self.assertIn(part, context)
        self.assertIn("Claude will now ask how to go on", paused["systemMessage"])
        self.assertIn('reply "continue" to resume in this session', paused["systemMessage"], "the notice still says how to resume later")
        self.assertIsNone(turn.paused_call({**self.post, "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}), "the question is not refused")

    def test_a_subagent_or_a_session_without_the_question_is_paused_as_before(self):
        subagent = self.pause(agent_id="a5e0c3d9b71f2a846")
        self.assertNotIn("AskUserQuestion", subagent["hookSpecificOutput"]["additionalContext"])
        constants.PAUSE_QUESTION = "off"
        plain = self.pause(session_id="s-plain", transcript_path=str(self.tmp / "s-plain.jsonl"))
        self.assertNotIn("AskUserQuestion", plain["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("Claude will now ask", plain["systemMessage"])
        constants.PAUSE_QUESTION = "auto"
        for entrypoint, wanted in (("cli", True), ("claude-vscode", True), ("claude-desktop", True), ("sdk-cli", False), ("", False)):
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": entrypoint}):
                self.assertIs(turn.question_wanted(self.post), wanted, entrypoint)

    def test_continue_renews_the_budget_in_place(self):
        self.pause()
        checkpoint = Path(self.current()["checkpoint"])
        checkpoint.write_text("- [ ] tests for the parser\n")
        self.assertTrue(self.refused())
        answered = self.answer("Continue here")
        self.assertIn("fresh budget of 3 steps", answered["hookSpecificOutput"]["additionalContext"])
        self.assertIn(str(checkpoint), answered["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(answered["systemMessage"], "Kiasi: continuing in this session with a fresh budget of 3 steps.")
        renewed = self.current()
        self.assertFalse(renewed["stopped"])
        self.assertEqual(renewed["steps"], 0, "the turn counts from zero again, after the question's own step")
        self.assertFalse(self.refused(), "calls run again")
        state = events.load_session(self.SID)
        self.assertNotIn("paused", state, "the pause is used up, so a later continue prompt resumes nothing")
        self.assertEqual(list(constants.NOTES_DIR.glob("*.paused*.json")), [])
        logged = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        resumes = [{k: e.get(k) for k in ("mode", "asked", "checklist", "steps")} for e in logged if e.get("event") == "turn_resume"]
        self.assertEqual(resumes, [{"mode": "resume", "asked": True, "checklist": True, "steps": 3}])
        again = self.pause()
        self.assertIn("kiasi paused this turn", again["hookSpecificOutput"]["additionalContext"], "the fresh budget pauses in its turn")
        self.assertEqual(self.current()["checkpoint"], str(checkpoint), "and its task file is the same one")

    def test_a_subagent_or_a_stop_leaves_the_turn_paused(self):
        self.pause()
        checkpoint = self.current()["checkpoint"]
        for reply, parts in (("Hand to a subagent", ("one Agent call with subagent_type general-purpose", checkpoint)),
                             ("Stop here", ("Make no more tool calls", f"Paused by Kiasi at 3 steps; the rest is in the task file {checkpoint}"))):
            answered = self.answer(reply)
            for part in parts:
                self.assertIn(part, answered["hookSpecificOutput"]["additionalContext"])
            self.assertNotIn("systemMessage", answered)
            self.assertEqual(self.current()["stopped"], 3)
            self.assertTrue(self.refused())
        self.assertIn("paused", events.load_session(self.SID), "a continue prompt later still resumes it")

    def test_an_answer_in_the_developers_own_words(self):
        self.pause()
        answered = self.answer("explain why it took so many steps first")
        self.assertIn('in their own words ("explain why it took so many steps first")', answered["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.current()["stopped"], 3)
        self.answer("ok, continue")
        self.assertFalse(self.current()["stopped"], "a typed continue is a continue")

    def test_the_choice_is_read_from_the_result_text_too(self):
        self.pause()
        self.answer(f'Your questions have been answered: "{self.QUESTION}"="Continue here". You can now continue with these answers in mind.')
        self.assertFalse(self.current()["stopped"])

    def warn(self):
        self.addCleanup(setattr, constants, "TURN_WARN_STEPS", constants.TURN_WARN_STEPS)
        constants.TURN_WARN_STEPS = 2
        return [turn.turn_guard(self.post, 1000) for _ in range(2)][-1]

    def test_the_warning_already_asks_so_claude_does_not_choose_for_the_developer(self):
        warned = self.warn()
        context = warned["hookSpecificOutput"]["additionalContext"]
        for part in ("make one AskUserQuestion call", "Kiasi's turn budget is nearly used: 2 of 3 steps", "Do not choose for the developer", *constants.PAUSE_CHOICES.values()):
            self.assertIn(part, context)
        self.assertIn("then to ask you how to go on", warned["systemMessage"])
        constants.PAUSE_QUESTION = "off"
        plain = [turn.turn_guard({**self.post, "session_id": "s-plain", "transcript_path": str(self.tmp / "s-plain.jsonl")}, 1000) for _ in range(2)][-1]
        self.assertNotIn("AskUserQuestion", plain["hookSpecificOutput"]["additionalContext"])

    def test_continue_at_the_warning_renews_the_budget_without_a_pause(self):
        self.warn()
        answered = self.answer("Continue here")
        self.assertIn("fresh budget of 3 steps", answered["hookSpecificOutput"]["additionalContext"])
        renewed = self.current()
        self.assertEqual((renewed["steps"], renewed["warned"], renewed["stopped"]), (0, False, False))
        logged = [json.loads(line)["event"] for line in open(constants.EVENT_LOG)]
        self.assertNotIn("turn_resume", logged, "nothing was paused, so nothing was resumed")
        self.assertNotIn("turn_stop", logged)

    def test_stop_or_a_subagent_at_the_warning_pauses_the_turn_there(self):
        self.warn()
        stopped = self.answer("Stop here")
        self.assertIn("Paused by Kiasi at 2 steps", stopped["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.current()["stopped"], 2)
        self.assertTrue(self.refused(), "a chosen stop refuses calls as the budget's pause does")
        state = events.load_session(self.SID)
        self.assertEqual(state["paused"]["steps"], 2, "replying continue later resumes it")
        chosen = [json.loads(line) for line in open(constants.EVENT_LOG) if '"turn_stop"' in line]
        self.assertEqual([(record["steps"], record.get("chosen")) for record in chosen], [(2, True)])

    def test_an_answer_in_their_own_words_at_the_warning_leaves_the_budget_alone(self):
        self.warn()
        answered = self.answer("first show me the diff")
        self.assertIn("first show me the diff", answered["hookSpecificOutput"]["additionalContext"])
        self.assertFalse(self.current()["stopped"])
        self.assertFalse(self.refused())

    def test_a_paused_turn_may_load_the_question_tool_and_nothing_else_through_tool_search(self):
        self.pause()
        search = {**self.post, "hook_event_name": "PreToolUse", "tool_name": "ToolSearch", "tool_input": {"query": "select:AskUserQuestion", "max_results": 1}}
        self.assertIsNone(turn.paused_call(search), "Claude Code defers the tool in some sessions")
        refused = turn.paused_call({**search, "tool_input": {"query": "select:WebSearch"}})
        self.assertEqual(refused["hookSpecificOutput"]["permissionDecision"], "deny")
        constants.PAUSE_QUESTION = "off"
        self.assertEqual(turn.paused_call(search)["hookSpecificOutput"]["permissionDecision"], "deny", "no question, nothing to load")

    def test_another_question_or_an_unpaused_turn_changes_nothing(self):
        self.assertNotIn("chose to continue", json.dumps(self.answer("Continue here")), "nothing was warned or paused")
        self.pause()
        other = self.answer("Continue here", header="Framework", labels=("Continue here", "React"))
        self.assertNotIn("fresh budget", json.dumps(other))
        self.assertEqual(self.current()["stopped"], 3)
        self.answer("Continue here", header="Reworded")
        self.assertFalse(self.current()["stopped"], "the three labels are enough when Claude reworded the header")

    def test_the_hook_carries_out_the_choice(self):
        self.pause()
        env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(self.tmp), "CLAUDE_PLUGIN_OPTION_PAUSE_QUESTION": "always", "CLAUDE_PLUGIN_OPTION_TURN_CALL_BUDGET": "3"}
        done = subprocess.run([sys.executable, str(SCRIPTS / "kiasi.py")], input=json.dumps(self.asked("Continue here")), env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertIn("fresh budget of 3 steps", json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"], done.stderr)
        self.assertFalse(self.current()["stopped"])


class TestAuditFixes(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.cwd = str(self.tmp / "app")
        (self.tmp / "app").mkdir()

    def post(self, sid, **extra):
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": sid, "cwd": self.cwd,
                "transcript_path": str(self.tmp / f"{sid}.jsonl"), **extra}

    def pause(self, sid):
        for _ in range(constants.TURN_STOP_STEPS):
            turn.turn_guard(self.post(sid), 1000)
        return Path(events.load_session(sid)["turns"][sid]["checkpoint"])

    def prompt(self, sid, text):
        return prompt.handle_prompt({"hook_event_name": "UserPromptSubmit", "prompt": text, "session_id": sid, "cwd": self.cwd,
                                     "transcript_path": str(self.tmp / f"{sid}.jsonl")}) or {}

    def test_parallel_refused_calls_are_one_refused_response(self):
        self.pause("s-par")
        state = events.load_session("s-par")
        state["batch_hook"] = ["turn"]
        events.save_session("s-par", state)
        pre = self.post("s-par", hook_event_name="PreToolUse", tool_name="Edit")
        for response in range(constants.TURN_DENY_BACKSTOP):
            ids = [f"toolu_{response}_{n}" for n in range(3)]
            with mock.patch("core.turn.time.time", return_value=1000.0 + response * 10):
                refused = [turn.handle_pre_tool_use({**pre, "tool_use_id": call_id}) for call_id in ids]
            ended = [r for r in refused if "continue" in r]
            if response < constants.TURN_DENY_BACKSTOP - 1:
                self.assertFalse(ended, "three calls of one response do not end the turn")
                batch = turn.handle_tool_batch({**pre, "hook_event_name": "PostToolBatch",
                                                "tool_calls": [{"tool_name": "Edit", "tool_use_id": c, "tool_response": "denied"} for c in ids]})
                self.assertIsNone(batch)
        self.assertEqual(len(ended), 3, "the third refused response ends the turn, counted by its calls without waiting for a batch")
        self.assertIs(ended[0]["continue"], False)

    def test_the_warned_checklist_stays_the_turns_checklist(self):
        warned = None
        for _ in range(constants.TURN_STOP_STEPS):
            result = turn.turn_guard(self.post("s-warn"), 1000) or {}
            if result and warned is None:
                warned = Path(events.load_session("s-warn")["turns"]["s-warn"]["checkpoint"])
                warned.parent.mkdir(parents=True, exist_ok=True)
                warned.write_text("- [ ] left\n")
        self.assertIsNotNone(warned)
        state = events.load_session("s-warn")
        self.assertEqual(state["turns"]["s-warn"]["checkpoint"], str(warned))
        self.assertEqual(state["paused"]["checkpoint"], str(warned), "a resume reads the checklist Claude wrote at the warning")

    def test_two_sessions_of_a_project_each_keep_their_pause(self):
        first, second = self.pause("s-one"), self.pause("s-two")
        self.assertEqual(len(list(constants.NOTES_DIR.glob("*.paused*.json"))), 2)
        first.write_text("- [ ] one\n")
        second.write_text("- [ ] two\n")
        self.assertIn(str(first), self.prompt("s-one", "continue")["hookSpecificOutput"]["additionalContext"])
        self.assertIn(str(second), self.prompt("s-two", "continue")["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(list(constants.NOTES_DIR.glob("*.paused*.json")), [])

    def test_a_pause_resumes_once_across_sessions(self):
        checkpoint = self.pause("s-orig")
        checkpoint.write_text("- [ ] left\n")
        session.handle_session_start({"hook_event_name": "SessionStart", "session_id": "s-new", "source": "clear", "cwd": self.cwd})
        self.assertIn(str(checkpoint), json.dumps(self.prompt("s-new", "continue")))
        self.assertNotIn(str(checkpoint), json.dumps(self.prompt("s-orig", "continue")), "the first resume used the pause up")

    def test_a_pause_record_that_cannot_be_written_still_trips_the_budget(self):
        with mock.patch.object(turn, "paused_file", side_effect=OSError("disk full")):
            self.pause("s-full")
        state = events.load_session("s-full")
        self.assertTrue(state["turns"]["s-full"]["stopped"])
        self.assertIn("paused", state)
        self.assertNotIn("recorded", state["paused"])
        logged = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        self.assertIn("pause_record", [e.get("kind") for e in logged if e.get("event") == "error"])

    def test_a_batch_of_a_cleared_turn_is_no_step_and_a_running_subagent_keeps_its_count(self):
        self.prompt("s-clear", "first task please")
        turn.handle_pre_tool_use({**self.post("s-clear", hook_event_name="PreToolUse"), "tool_use_id": "toolu_old"})
        state = events.load_session("s-clear")
        state["turns"]["agent-key"] = {"steps": 5, "reread": 5000, "warned": False, "stopped": False, "index": 1}
        events.save_session("s-clear", state)
        self.prompt("s-clear", "second task please")
        turn.handle_tool_batch({**self.post("s-clear", hook_event_name="PostToolBatch"),
                                "tool_calls": [{"tool_name": "Bash", "tool_use_id": "toolu_old", "tool_response": "ok"}]})
        state = events.load_session("s-clear")
        self.assertEqual(state["turns"]["s-clear"]["steps"], 0)
        self.assertEqual(state["turns"]["agent-key"]["steps"], 5)

    def test_an_unusable_project_setting_keeps_its_default_and_is_logged_once(self):
        keys = list(constants.PROJECT_KEYS)
        for name in constants.PROJECT_KEYS.values():
            self.addCleanup(setattr, constants, name, getattr(constants, name))
        default = getattr(constants, constants.PROJECT_KEYS[keys[0]])
        (self.tmp / "app" / constants.PROJECT_FILE_NAME).write_text('{"%s": 1e999, "%s": 7}' % (keys[0], keys[1]))
        for _ in range(2):
            applied = constants.apply_project(self.cwd)
        self.assertEqual(getattr(constants, constants.PROJECT_KEYS[keys[0]]), default)
        self.assertEqual(applied, {constants.PROJECT_KEYS[keys[1]]: 7}, "the other settings still apply")
        logged = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        self.assertEqual([e["kind"] for e in logged if e.get("event") == "error"], ["bad_setting"])

    def test_the_outside_read_pattern_takes_both_separators(self):
        import re
        for path in ("/home/a/.claude/projects/x/y.txt", "C:\\Users\\a\\.claude\\projects\\x\\y.txt", "C:\\Users\\a\\AppData\\Local\\Temp\\z.txt"):
            self.assertTrue(re.search(constants.OUTSIDE_READ_PATTERN, path), path)


class TestPauseChecklistAndSubagentTurns(KiasiTestCase):
    def test_the_checklist_can_be_read_and_edited_at_a_pause(self):
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-check", "transcript_path": str(self.tmp / "s-check.jsonl")}
        for _ in range(constants.TURN_STOP_STEPS):
            turn.turn_guard(post, 1000)
        checkpoint = events.load_session("s-check")["turns"]["s-check"]["checkpoint"]
        pre = {**post, "hook_event_name": "PreToolUse", "tool_use_id": "t1"}
        self.assertIsNone(turn.paused_call({**pre, "tool_name": "Read", "tool_input": {"file_path": checkpoint}}))
        self.assertIsNone(turn.paused_call({**pre, "tool_name": "Edit", "tool_input": {"file_path": checkpoint, "old_string": "a", "new_string": "b"}}))
        other = turn.paused_call({**pre, "tool_name": "Edit", "tool_input": {"file_path": str(self.tmp / "other.md"), "old_string": "a", "new_string": "b"}})
        self.assertEqual(other["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(f"Bring the task file at {checkpoint} up to date (Read it, then Edit it", other["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(events.load_session("s-check")["turns"]["s-check"].get("denied", 0), 1, "checklist calls are not refusals")
        self.assertIn("the task file's own Write, Read and Edit is refused", session.rules_text())

    def test_subagent_turns_of_older_prompts_are_dropped(self):
        main = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-old", "transcript_path": str(self.tmp / "s-old.jsonl")}
        turn.turn_guard(main, 1000)
        turn.turn_guard({**main, "agent_id": "a1fd597a875bd1f14"}, 1000)
        prompt.handle_prompt({"prompt": "next task", "session_id": "s-old", "transcript_path": main["transcript_path"]})
        self.assertIn("a1fd597a875bd1f14", events.load_session("s-old")["turns"], "a subagent of the previous prompt may still be running")
        prompt.handle_prompt({"prompt": "another task", "session_id": "s-old", "transcript_path": main["transcript_path"]})
        self.assertNotIn("a1fd597a875bd1f14", events.load_session("s-old")["turns"], "a subagent two prompts back is done")

    def test_the_task_file_and_the_old_checkpoint_path_both_pass_at_a_pause(self):
        old = str(self.tmp / "old-checkpoint.md")
        state = {"checkpoint": old, "stopped": 3}
        call = {"tool_name": "Edit", "session_id": "s-both", "tool_input": {"file_path": str(taskfile.path_for(None, "s-both"))}}
        self.assertTrue(turn.checklist_call(call, state), "the task file is named at the warning")
        self.assertTrue(turn.checklist_call({**call, "tool_input": {"file_path": old}}, state), "the old path is accepted for one release")
        self.assertFalse(turn.checklist_call({**call, "tool_input": {"file_path": str(self.tmp / "other.md")}}, state))
        post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-both", "transcript_path": str(self.tmp / "s-both.jsonl")}
        warned = [r["hookSpecificOutput"]["additionalContext"] for r in (turn.turn_guard(post, 1000) for _ in range(constants.TURN_STOP_STEPS)) if r]
        self.assertIn(str(taskfile.path_for(None, "s-both")), warned[0])
        self.assertIn("task file", warned[0])
        self.assertNotIn("as a checklist", warned[0])
