"""Subscription usage limits for the dashboard header, from the status line only.

Claude Code hands every status line command its rate_limits (5-hour and weekly
use, Pro and Max plans). The Kiasi wrapper (statusline.py) saves them to
rate-limits.json; a hand-made status line script may write limits.json. Both
are normalised to one shape, and the newest reading wins row by row:

    {"updated": epoch, "source": "statusline" | None,
     "limits": [{"key", "label", "group", "used", "resets_at", "severity", "active"}],
     "setup": "none" | "waiting" | "ready"}
"""
import json
import time
from datetime import datetime

from core import constants


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _epoch(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _severity(used):
    if used is None:
        return "normal"
    if used >= constants.LIMIT_CRITICAL_PERCENT:
        return "critical"
    return "warning" if used >= constants.LIMIT_WARN_PERCENT else "normal"


def _item(key, label, group, used, resets_at):
    used = None if used is None else round(float(used))
    return {"key": key, "label": label, "group": group, "used": used,
            "resets_at": _epoch(resets_at), "severity": _severity(used), "active": False}


def _window_label(field):
    if field == "five_hour":
        return "5-hour", "session"
    if field == "seven_day":
        return "Weekly", "weekly"
    if field.startswith("seven_day_"):
        return f"Weekly · {field[len('seven_day_'):].replace('_', ' ').title()}", "weekly"
    return None, None


def normalize_rate_limits(data):
    items = []
    for field, window in ((data or {}).get("rate_limits") or {}).items():
        label, group = _window_label(field)
        if label and isinstance(window, dict) and window.get("used_percentage") is not None:
            items.append(_item(field, label, group, window["used_percentage"], window.get("resets_at")))
    if not items:
        return None
    return {"updated": int(data.get("updated") or 0), "source": "statusline", "limits": items}


def normalize_statusline(data):
    items = []
    for field in ("five_hour", "seven_day"):
        label, group = _window_label(field)
        window = (data or {}).get(field) or {}
        if window.get("used") is not None:
            items.append(_item(field, label, group, window["used"], window.get("resets_at")))
    if not items:
        return None
    return {"updated": int(data.get("updated") or 0), "source": "statusline", "limits": items}


def _history_points():
    points = []
    try:
        with open(constants.DATA_DIR / constants.LIMITS_HISTORY_NAME, encoding="utf-8") as fh:
            for line in fh:
                try:
                    points.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return points


def _forecast(points, item, now):
    """Linear burn from the first and last reading of the current window."""
    span = constants.LIMIT_GROUP_SPAN.get(item["group"])
    resets = item.get("resets_at")
    if not span or not resets or item.get("used") is None:
        return None
    mine = [p for p in points
            if p.get("key") == item["key"] and p.get("used") is not None
            and resets - span <= p.get("ts", 0) <= now]
    if len(mine) < constants.FORECAST_MIN_POINTS:
        return None
    first, last = mine[0], mine[-1]
    elapsed, climb = last["ts"] - first["ts"], last["used"] - first["used"]
    if elapsed < constants.FORECAST_MIN_SPAN_SECONDS or climb <= 0:
        return None
    rate = climb / elapsed
    return {"run_out_at": int(last["ts"] + (100 - last["used"]) / rate),
            "burn_per_day": round(rate * 86400, 1)}


def add_forecast(reading):
    points = _history_points()
    now = int(time.time())
    for item in reading["limits"]:
        forecast = _forecast(points, item, now)
        if forecast:
            item.update(forecast)
            if forecast["run_out_at"] < (item.get("resets_at") or 0) and item["severity"] == "normal":
                item["severity"] = "warning"
    return reading


def statusline_installed():
    return (constants.HOME_DIR / constants.STATUSLINE_CHAIN_NAME).is_file()


def get_limits():
    readings = [normalize_rate_limits(_read_json(constants.DATA_DIR / constants.RATE_LIMITS_NAME)),
                normalize_statusline(_read_json(constants.DATA_DIR / constants.LEGACY_LIMITS_NAME))]
    readings = sorted((r for r in readings if r), key=lambda r: r["updated"], reverse=True)
    if not readings:
        return {"updated": 0, "source": None, "limits": [],
                "setup": "waiting" if statusline_installed() else "none"}
    best = readings[0]
    for older in readings[1:]:
        best = _merge(best, older)
    return add_forecast(dict(best, setup="ready"))


def _merge(newer, older):
    """Newer rows win by label; rows only the older reading has are kept with their own time."""
    seen = {item["label"] for item in newer["limits"]}
    carried = [dict(item, as_of=item.get("as_of") or older.get("updated", 0))
               for item in older.get("limits") or [] if item["label"] not in seen]
    return dict(newer, limits=newer["limits"] + carried)
