

import json
import os
import subprocess
import sys
import time

from helpers import SCRIPTS, KiasiTestCase
from core import constants  # noqa: E402
from core import events  # noqa: E402
from core import prompt  # noqa: E402
from core import session  # noqa: E402
from core import turn  # noqa: E402


class TestTurnGuard(KiasiTestCase):
    def test_pauses_at_the_budget_with_a_notice_instead_of_an_error(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "sess-turn",
                   "transcript_path": str(self.tmp / "missing-transcript.jsonl")}
        results = [turn.turn_guard(payload, 100_000) for _ in range(constants.TURN_STOP_STEPS)]
        warning, paused = results[constants.TURN_WARN_STEPS - 1], results[-1]
        self.assertIn(f"pauses at {constants.TURN_STOP_STEPS}", warning["systemMessage"])
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
        self.assertEqual([n for n, _ in said], [constants.SUBAGENT_STEP_LIMIT // 2, constants.SUBAGENT_STEP_LIMIT])
        self.assertIn(f"pauses at {constants.SUBAGENT_STEP_LIMIT} tool calls", said[0][1])
        self.assertIn("kiasi paused this subagent", said[1][1])
        self.assertIn("reply to the caller", said[1][1])
        self.assertNotIn("Agent call", said[1][1], "a subagent cannot start another subagent")
        main_results = [turn.turn_guard(main, 1000) for _ in range(constants.SUBAGENT_STEP_LIMIT)]
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
