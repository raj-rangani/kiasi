#!/usr/bin/env python3
"""Run work out of context, the way the mcp__kiasi__run and mcp__kiasi__distill tools do.

`run` executes a shell command, saves the full output under outputs/ (searchable like any
other saved output) and prints only a digest: exit code, head, error lines, tail, saved path.
`distill` executes a short python3 or node script over files passed as arguments and prints
only what the script printed, capped; the overflow is saved and named.
`fetch` downloads a URL, strips HTML to text, saves the whole page under outputs/ and prints
only its head plus the lines matching the words asked for.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import constants


def log_saving(tool, raw_chars, shown_chars, saved_path, label, session=""):
    if raw_chars <= shown_chars:
        return
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "cap", "session_id": session, "tool_name": tool,
              "kind": "sandbox", "chars": raw_chars, "shown_chars": shown_chars, "saved_path": str(saved_path or ""),
              "label": label[:120]}
    with open(constants.EVENT_LOG, "a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def save(kind, text):
    constants.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = constants.OUTPUT_DIR / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.txt"
    path.write_text(text, errors="replace")
    return path


def clip(line):
    if len(line) <= constants.RUN_LINE_CHARS:
        return line
    return line[: constants.RUN_LINE_CHARS] + " …"


def digest(text, saved_path):
    lines = text.splitlines()
    head, tail = constants.RUN_HEAD_LINES, constants.RUN_TAIL_LINES
    if len(lines) <= head + tail:
        return [clip(line) for line in lines]
    middle = lines[head:-tail]
    errors = [clip(line) for line in middle if re.search(constants.ERROR_LINE_PATTERN, line)]
    kept = [clip(line) for line in lines[:head]]
    kept.append(f"[kiasi trimmed {len(middle)} of {len(lines)} lines here; full output saved at {saved_path}]")
    if errors:
        kept.append(f"error lines from the trimmed part ({min(len(errors), constants.RUN_ERROR_LINES)} of {len(errors)}):")
        kept.extend(errors[: constants.RUN_ERROR_LINES])
    kept.extend(clip(line) for line in lines[-tail:])
    return kept


def run(args):
    start = time.time()
    try:
        proc = subprocess.run(["bash", "-lc", args.command], capture_output=True, text=True,
                              errors="replace", timeout=args.timeout)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = f"kiasi: command timed out after {args.timeout}s"
    text = out + (f"\n--- stderr ---\n{err}" if err.strip() else "")
    saved = save("run", f"$ {args.command}\n\n{text}")
    shown = (f"exit {code} in {time.time() - start:.1f}s; {len(text.splitlines())} lines, {len(text)} chars; "
             f"full output saved at {saved}, search it with mcp__kiasi__search\n" + "\n".join(digest(text, saved)))
    log_saving("mcp__kiasi__run", len(text), len(shown), saved, args.command, args.session)
    print(shown)
    return 1 if code else 0  # non-zero exit lets the hook flag the result as an error; the digest stays


def distill(args):
    argv = {"python": [sys.executable, "-c"], "node": ["node", "-e"]}[args.language] + [args.code] + args.file
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"kiasi: script timed out after {args.timeout}s")
        return 1
    if proc.returncode != 0:
        print(f"script failed (exit {proc.returncode}): {proc.stderr.strip()[-constants.DISTILL_ERROR_CHARS:]}")
        return 1
    out = proc.stdout
    read_avoided = sum(os.path.getsize(f) for f in args.file if os.path.isfile(f))
    if len(out) <= constants.DISTILL_RESULT_CHARS:
        shown = out.rstrip() or "(the script printed nothing)"
        log_saving("mcp__kiasi__distill", max(read_avoided, len(out)), len(shown), "", " ".join(args.file) or args.code, args.session)
        print(shown)
        return 0
    saved = save("distill", out)
    shown = (out[: constants.DISTILL_RESULT_CHARS].rstrip() +
             f"\n[kiasi kept the first {constants.DISTILL_RESULT_CHARS} of {len(out)} chars the script printed; "
             f"full text saved at {saved}; print less, or read it by section]")
    log_saving("mcp__kiasi__distill", max(read_avoided, len(out)), len(shown), saved, " ".join(args.file) or args.code, args.session)
    print(shown)
    return 0


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = ""
        self.skip = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in constants.FETCH_SKIP_TAGS:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in constants.FETCH_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in constants.FETCH_SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False
        if tag in constants.FETCH_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def html_to_text(html):
    parser = TextExtractor()
    parser.feed(html)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return parser.title.strip(), "\n".join(line for line in lines if line)


def download(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": constants.FETCH_USER_AGENT, "Accept": "text/html,application/json,text/plain,*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(constants.FETCH_MAX_BYTES + 1)
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
    truncated = len(raw) > constants.FETCH_MAX_BYTES
    return raw[: constants.FETCH_MAX_BYTES].decode(charset, errors="replace"), content_type, truncated


def find_lines(lines, words):
    wanted = [w.lower() for w in words if w]
    if not wanted:
        return []
    hits = [f"{number}: {clip(line)}" for number, line in enumerate(lines, 1) if any(w in line.lower() for w in wanted)]
    shown = hits[: constants.FETCH_FIND_LINES]
    header = f"lines matching {', '.join(words)} ({len(shown)} of {len(hits)}):" if hits else f"no line matches {', '.join(words)}"
    return [header, *shown]


def fetch(args):
    try:
        body, content_type, truncated = download(args.url, args.timeout)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        print(f"fetch failed: {exc}")
        return 1
    title = ""
    if "html" in content_type or body.lstrip()[:1] == "<":
        title, body = html_to_text(body)
    saved = save("fetch", f"{args.url}\n\n{body}")
    lines = body.splitlines()
    head = body[: constants.FETCH_HEAD_CHARS].rstrip()
    if len(body) > constants.FETCH_HEAD_CHARS:
        head += f"\n[kiasi kept the first {constants.FETCH_HEAD_CHARS} of {len(body)} chars; the rest is in the saved file]"
    cut = f", cut at {constants.FETCH_MAX_BYTES} bytes" if truncated else ""
    shown = "\n".join([f"fetched {args.url}: {title or content_type}; {len(body)} chars, {len(lines)} lines{cut}; "
                       f"full text saved at {saved}, search it with mcp__kiasi__search", head, *find_lines(lines, args.find)])
    log_saving(constants.FETCH_TOOL_NAME, len(body), len(shown), saved, args.url, args.session)
    print(shown)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--command", required=True)
    p_run.add_argument("--timeout", type=int, default=constants.SANDBOX_TIMEOUT_SECONDS)
    p_run.add_argument("--session", default="")
    p_run.set_defaults(handler=run)
    p_distill = sub.add_parser("distill")
    p_distill.add_argument("--language", choices=("python", "node"), default="python")
    p_distill.add_argument("--code", required=True)
    p_distill.add_argument("--file", action="append", default=[])
    p_distill.add_argument("--timeout", type=int, default=constants.SANDBOX_TIMEOUT_SECONDS)
    p_distill.add_argument("--session", default="")
    p_distill.set_defaults(handler=distill)
    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("--url", required=True)
    p_fetch.add_argument("--find", action="append", default=[])
    p_fetch.add_argument("--timeout", type=int, default=constants.SANDBOX_TIMEOUT_SECONDS)
    p_fetch.add_argument("--session", default="")
    p_fetch.set_defaults(handler=fetch)
    args = parser.parse_args()
    args.timeout = max(1, min(constants.SANDBOX_TIMEOUT_MAX_SECONDS, args.timeout))
    sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
