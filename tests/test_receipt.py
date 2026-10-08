import json
import time

from helpers import KiasiTestCase, constants
from core import events, receipt, session


def stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class ReceiptCase(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.shown = []
        for module in (receipt, session):
            self.addCleanup(setattr, module, "notify_desktop", module.notify_desktop)
            module.notify_desktop = lambda title, body: self.shown.append((title, body))
        self.sid = "sess-receipt-1"
        self.cwd = str(self.tmp / "proj")
        base = time.time() - 600
        self.transcript = self.tmp / "t.jsonl"
        self.transcript.write_text("".join(json.dumps({"type": kind, "timestamp": stamp(base + i * 60)}) + "\n"
                                           for i, kind in enumerate(["user", "assistant", "assistant", "assistant"])))
        # a cap after the first assistant step: 8000 chars cut to 800, so 1800 tokens kept out of the 2 later steps
        events.log_event({"ts": stamp(base + 90), "event": "cap", "session_id": self.sid, "tool_name": "Bash", "kind": "sandbox",
                          "chars": 8000, "shown_chars": 800, "label": "npm install"})
        events.save_session(self.sid, {"turns": {"a": {"steps": 3, "reread": 100_000, "warned": False, "stopped": False, "index": 0}}})

    def payload(self, **extra):
        return {"session_id": self.sid, "transcript_path": str(self.transcript), "cwd": self.cwd, **extra}


class TestCapEpoch(KiasiTestCase):
    def test_local_and_utc_stamps_agree(self):
        now = int(time.time())
        local = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
        self.assertEqual(receipt.cap_epoch(local), now)
        self.assertEqual(receipt.cap_epoch(stamp(now)), now)
        self.assertEqual(receipt.cap_epoch(""), 0)


class TestBuildReceipt(ReceiptCase):
    def test_counts_kept_out_and_the_re_reads_it_avoided(self):
        r = receipt.build_receipt(self.sid, str(self.transcript), self.cwd, events.load_session(self.sid))
        self.assertEqual((r["sent"], r["steps"], r["prompts"]), (100_000, 3, 1))
        self.assertEqual(r["kept_out"], 1800)
        self.assertEqual(r["avoided"], 3600, "1800 tokens times the 2 assistant steps after the cap")
        self.assertEqual(r["would_have"], 103_600)
        self.assertEqual(r["top"]["label"], "npm install")
        text = receipt.receipt_text(r)
        self.assertIn("sent 100 k of context across 3 steps", text)
        self.assertIn("Without Kiasi about 104 k", text)
        self.assertIn("1.8 k kept out, 3.6 k of re-reads avoided", text)
        self.assertIn("Costliest output: Bash npm install (1.8 k tokens cut)", text)
        self.assertEqual([receipt.fmt_tokens(n) for n in (0, 950, 1800, 103_600, 6_900_000, 373_000_000, 2_844_918_291)], ["0", "950", "1.8 k", "104 k", "6.9 M", "373 M", "2.84 B"])

    def test_a_session_with_nothing_sent_has_no_receipt(self):
        r = receipt.build_receipt("other", None, self.cwd, {})
        self.assertEqual(receipt.receipt_text(r), "")


class TestSessionEnd(ReceiptCase):
    def test_writes_the_receipt_notifies_and_logs(self):
        self.assertIsNone(receipt.handle_session_end(self.payload()))
        [row] = receipt.read_receipts()
        self.assertEqual((row["session"], row["cwd"], row["kept_out"]), (self.sid, self.cwd, 1800))
        self.assertEqual(self.shown[0][0], "Kiasi receipt")
        log = constants.EVENT_LOG.read_text()
        self.assertIn('"event": "receipt"', log)

    def test_subagents_and_empty_sessions_write_nothing(self):
        receipt.handle_session_end(self.payload(agent_id="sub"))
        receipt.handle_session_end({"session_id": "empty", "cwd": self.cwd})
        self.assertEqual(receipt.read_receipts(), [])
        self.assertEqual(self.shown, [])

    def test_keeps_the_newest_rows(self):
        constants.RECEIPTS_KEEP = 3
        self.addCleanup(setattr, constants, "RECEIPTS_KEEP", 200)
        for i in range(5):
            receipt.write_receipt({"session": f"s{i}", "cwd": self.cwd, "kept_out": 1})
        self.assertEqual([r["session"] for r in receipt.read_receipts()], ["s2", "s3", "s4"])


class TestNextStart(ReceiptCase):
    def test_the_last_receipt_here_shows_at_startup_only(self):
        receipt.handle_session_end(self.payload())
        lines = session.developer_lines({"session_id": "sess-new", "cwd": self.cwd, "source": "startup"}, None)
        self.assertTrue(any(line.startswith("Kiasi receipt for the last session here") for line in lines), lines)
        self.assertEqual(session.developer_lines({"session_id": "sess-new", "cwd": self.cwd, "source": "resume"}, None), [])
        self.assertEqual(session.developer_lines({"session_id": "sess-new", "cwd": str(self.tmp / "elsewhere"), "source": "startup"}, None), [])

    def test_the_same_session_does_not_see_its_own_receipt(self):
        receipt.handle_session_end(self.payload())
        self.assertEqual(session.developer_lines({"session_id": self.sid, "cwd": self.cwd, "source": "startup"}, None), [])
