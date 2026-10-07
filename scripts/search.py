#!/usr/bin/env python3
"""Search Kiasi's saved outputs, pastes, notes and checkpoints by reading them directly.

There is no index: the folder stays small (cleanup.py keeps it to about a week of sessions) and a
scan of all of it takes milliseconds, so a stored index would only be a second copy of the text.
"""
import argparse
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import constants
from core.events import checklist_folder


def search_dirs():
    """Kiasi's data folders, and the checklist folder of the project the search runs in."""
    folders = list(constants.SEARCH_DIRS)
    project = checklist_folder(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    return folders if project in folders else folders + [project]


def iter_files():
    for folder in search_dirs():
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in constants.SEARCH_SUFFIXES:
                yield path


def read_body(path):
    try:
        return path.read_text(errors="replace")[: constants.SEARCH_MAX_FILE_CHARS]
    except OSError:
        return ""


def term_pattern(term):
    """A word matches itself and its common endings; `word*` matches any ending; a quoted phrase matches its words in order."""
    if term.endswith("*") and len(term) > 1:
        return re.compile(r"\b" + re.escape(term[:-1]), re.I)
    words = re.findall(r"\w+", term) or [term]
    body = r"\W+".join(re.escape(word) for word in words)
    return re.compile(r"\b" + body + constants.SEARCH_WORD_ENDINGS + r"\b", re.I)


def parse(words):
    """[(kind, patterns)] with kind 'all', 'any' or 'none': plain terms must all appear, `a OR b` needs one, `NOT x` excludes."""
    tokens = re.findall(r'"[^"]*"|\S+', " ".join(words))
    clauses, group, negate = [], None, False
    for token in tokens:
        upper = token.upper()
        if upper in ("AND", "NEAR"):
            continue
        if upper == "NOT":
            negate = True
            continue
        if upper == "OR" and clauses and clauses[-1][0] != "none":
            group = clauses.pop()
            continue
        term = token.strip('"')
        if not term.strip():
            continue
        pattern = term_pattern(term)
        if negate:
            clauses.append(("none", [pattern]))
        elif group:
            clauses.append(("any", group[1] + [pattern]))
        else:
            clauses.append(("all", [pattern]))
        group, negate = None, False
    if group:
        clauses.append(group)
    return clauses


def matches(body, clauses):
    """Hit counts per pattern, or None when the file fails a clause."""
    counts = {}
    for kind, patterns in clauses:
        found = {pattern: len(pattern.findall(body)) for pattern in patterns}
        if kind == "none":
            if any(found.values()):
                return None
            continue
        if not any(found.values()):
            return None
        counts.update(found)
    return counts


def hit_lines(body, patterns):
    """Sorted 1-based line numbers of every hit, so the file can be read by section."""
    return sorted({body.count("\n", 0, m.start()) + 1 for p in patterns for m in p.finditer(body)})


def snippet(body, patterns):
    """About SEARCH_SNIPPET_TOKENS words around the first hit, with every hit in [brackets]."""
    first = min((m.start() for m in (p.search(body) for p in patterns) if m), default=0)
    size = constants.SEARCH_SNIPPET_TOKENS
    start = max(0, first - size * 3)
    parts = body[start:first + size * 6].split()
    if start and len(parts) > 1:
        parts = parts[1:]
    text = " ".join(parts[:size])
    for pattern in patterns:
        text = pattern.sub(lambda m: f"[{m.group(0)}]", text)
    return ("… " if start else "") + text + (" …" if len(parts) > size else "")


def search(words, limit):
    """([(path, snippet, score, lines)] best first, files scanned); rarer words and repeated hits score higher, as in bm25."""
    clauses = parse(words)
    wanted = [p for kind, patterns in clauses if kind != "none" for p in patterns]
    if not wanted:
        return [], 0
    hits, total = [], 0
    for path in iter_files():
        total += 1
        body = read_body(path)
        counts = matches(body, clauses)
        if counts is not None:
            hits.append((path, body, counts))
    df = {p: sum(1 for _, _, counts in hits if counts.get(p)) for p in wanted}
    rows = []
    for path, body, counts in hits:
        length = max(1, len(body) / constants.SEARCH_AVG_CHARS)
        found = [p for p in wanted if counts.get(p)]
        score = sum(math.log(1 + total / df[p]) * counts[p] / (counts[p] + length) for p in found)
        rows.append((str(path), snippet(body, found), score, hit_lines(body, found)))
    rows.sort(key=lambda row: -row[2])
    return rows[:limit], total


def main():
    parser = argparse.ArgumentParser(description="Search Kiasi's saved outputs, pastes, notes and checkpoints.")
    parser.add_argument("words", nargs="*")
    parser.add_argument("-n", "--limit", type=int, default=constants.SEARCH_RESULTS)
    args, extra = parser.parse_known_args()
    args.words += extra  # a word like -bash: is a word, not an unknown flag
    if not args.words:
        print(f"{sum(1 for _ in iter_files())} files searchable in {', '.join(str(d) for d in search_dirs())}")
        return
    rows, total = search(args.words, args.limit)
    if not rows:
        print(f"no match in {total} files")
        return
    for path, text, score, lines in rows:
        shown = ", ".join(str(n) for n in lines[: constants.SEARCH_HIT_LINES])
        extra = f" +{len(lines) - constants.SEARCH_HIT_LINES} more" if len(lines) > constants.SEARCH_HIT_LINES else ""
        print(f"{path}  ({score:.1f})  lines {shown}{extra}")
        print("  " + " ".join(text.split()))


if __name__ == "__main__":
    main()
