import hashlib

from core import constants


def holdout_off(session_id, rule, cwd=None):
    """True when the configured holdout (env or .kiasi.json) is rule and this session id falls in the odd half."""
    if cwd:
        constants.apply_project(cwd)
    if constants.HOLDOUT != rule:
        return False
    return int(hashlib.sha1(str(session_id).encode()).hexdigest(), 16) % 2 == 1


def held_off(state, rule):
    """The session's recorded holdout, read from its state: true when rule is the held-out one and this session is in the off half."""
    held = state.get("holdout") or {}
    return held.get("rule") == rule and bool(held.get("off"))
