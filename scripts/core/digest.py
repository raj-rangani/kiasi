"""The runway sentence, the days of limit Kiasi bought back, and the weekly digest.

Runway reads the live limit windows (limits_data) and says whether the weekly limit lasts to its reset at the
current burn. Bought-back converts the tokens Kiasi kept out into days of the weekly limit at the measured daily
rate. The digest joins both with the report's since-install factor, spikes and next fix, once a week at SessionStart."""
import gzip
import json
import time

from core import constants
from core.receipt import fmt_tokens

WEEK = 7 * 86400
PLAIN_KIND = {"floor": "the startup floor", "system reminders": "system reminders", "file reads": "whole-file reads", "shell output": "shell output",
              "tool calls": "tool calls", "subagent results": "subagent results", "web pages": "web pages", "pastes": "pastes", "compaction summary": "the compaction summary"}


def when(epoch):
    return time.strftime("%a %H:%M", time.localtime(epoch))


def runway(reading, now=None):
    """From a limits reading: the weekly window's forecast, or None when there is no weekly reading.

    state: runs_out (the forecast ends before the reset), clear (it reaches the reset with spare% left),
    pace (no forecast yet; used against the share of the window gone)."""
    now = now or time.time()
    items = [i for i in (reading or {}).get("limits") or [] if i.get("group") == "weekly" and i.get("used") is not None]
    if not items:
        return None
    item = sorted(items, key=lambda i: i.get("key") != "seven_day")[0]
    resets = item.get("resets_at")
    if not resets or resets < now:
        return None
    used = max(0, min(100, int(item["used"])))
    left_days = (resets - now) / 86400
    out = {"label": item.get("label") or "Weekly", "used": used, "resets_at": int(resets), "run_out_at": item.get("run_out_at"),
           "burn_per_day": item.get("burn_per_day")}
    if item.get("run_out_at") and item["run_out_at"] < resets:
        return dict(out, state="runs_out")
    if item.get("burn_per_day"):
        spare = max(0, min(100, round(100 - used - item["burn_per_day"] * left_days)))
        return dict(out, state="clear", spare=spare)
    gone = round((1 - left_days * 86400 / WEEK) * 100)
    return dict(out, state="pace", gap=used - gone)


def runway_sentence(r):
    if not r:
        return ""
    if r["state"] == "runs_out":
        return f"At this pace the weekly limit runs out {when(r['run_out_at'])}, before the reset {when(r['resets_at'])}."
    if r["state"] == "clear":
        return f"At this pace you reach the reset {when(r['resets_at'])} with about {r['spare']}% of the weekly limit to spare."
    gap = r["gap"]
    pace = "on pace" if abs(gap) <= constants.LIMIT_PACE_SLACK else f"{abs(gap)} pts {'ahead of' if gap > 0 else 'under'} pace"
    return f"{r['used']}% of the weekly limit used, {pace}; it resets {when(r['resets_at'])}."


def bought_back(saved, rate):
    """Days of the weekly limit the avoided re-reads amount to at the measured daily rate; None without a rate.

    saved is the report's sum of kept-out tokens times the steps that followed each cut: the context that was not re-sent."""
    if not rate or saved is None:
        return None
    return round(saved / rate, 1)


def bought_back_text(days):
    if days is None:
        return ""
    if days < 0.1:
        return f"about {round(days * 24, 1)} hours"
    return f"about {days} day{'' if days == 1 else 's'}"


def load_lens():
    try:
        with gzip.open(constants.LENS_FILE, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, EOFError):
        return None


def digest_text(lens, limits_reading=None):
    """The weekly digest from a built report, or '' when the report has under a week of days."""
    if not lens or len(lens.get("per_day") or []) < constants.DIGEST_MIN_DAYS:
        return ""
    t, s, p = lens.get("totals") or {}, lens.get("since") or {}, lens.get("pace") or {}
    lines = []
    if s.get("factor") is not None:
        f = s["factor"]
        lines.append(f"Each request sends {f}× less context than before Kiasi." if f >= 1 else f"Each request sends {round(1 / f, 1)}× more context than before Kiasi.")
    kept, saved = t.get("kept_out") or 0, t.get("saved") or 0
    days = bought_back(saved, p.get("rate"))
    if kept:
        lines.append(f"Kiasi kept {fmt_tokens(kept)} tokens out of your context this week and avoided {fmt_tokens(saved)} of re-reads"
                     + (f", {bought_back_text(days)} of your weekly limit." if days is not None else "."))
    if p.get("rate") is not None and p.get("prior_rate"):
        change = round((p["rate"] - p["prior_rate"]) * 100 / p["prior_rate"])
        lines.append(f"Context sent a day: {fmt_tokens(p['rate'])}, {abs(change)}% {'more' if change > 0 else 'less'} than the week before.")
    spikes = (lens.get("spikes") or {}).get("rows") or []
    if spikes:
        top = spikes[0]
        lines.append(f"{len(spikes)} spike session{'s' if len(spikes) > 1 else ''}: {str(top.get('day', ''))[5:10]} sent {fmt_tokens(top.get('reread', 0))}, {top.get('factor')}× the usual.")
    kinds = (lens.get("attribution") or {}).get("kinds") or []
    if kinds:
        lines.append(f"Next fix: {PLAIN_KIND.get(kinds[0]['kind'], kinds[0]['kind'])}, {round(kinds[0]['share'] * 100)}% of the context sent.")
    sentence = runway_sentence(runway(limits_reading))
    if sentence:
        lines.append(sentence)
    return "\n".join(lines)


def read_marker():
    try:
        return json.loads(constants.DIGEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def due(now=None):
    last = read_marker().get("last") or 0
    return (now or time.time()) - last >= constants.DIGEST_DAYS * 86400


def mark(now=None):
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    constants.DIGEST_FILE.write_text(json.dumps({"last": int(now or time.time())}), encoding="utf-8")


def weekly_digest(limits_reading=None, now=None):
    """The digest text when one is due and a report exists; marks it shown. '' otherwise."""
    if not due(now):
        return ""
    text = digest_text(load_lens(), limits_reading)
    if text:
        mark(now)
    return text
