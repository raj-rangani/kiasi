"""End-to-end check: after a pruned compaction, every result of a parallel tool batch is still there.

Claude Code writes a parallel batch as one assistant entry per call, all sharing a message id, and writes each
result when its call ends. hooks/prune.js must rebuild such a batch as one message ahead of all its results;
otherwise every call but the last loads as "[Tool result missing due to internal error]".

It runs a real session of about eight short Haiku calls, so it is not part of the unit tests. Run it by hand after
changing hooks/prune.js or after a Claude Code update:

    python3 tests/e2e_parallel_compact.py [plugin dir, default: this repo]

The plugin dir replaces CLAUDE_CODE_PLUGIN_DIRS for the test session; disable any other installed copy of kiasi.
Exit 0: every result survived. 1: a result was lost. 2: inconclusive, the session did not set up the case.
"""
import glob
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else ROOT
ENV = {'CLAUDE_CODE_PLUGIN_DIRS': PLUGIN, 'CLAUDE_CODE_ENABLE_FUNCTION_HOOKS': '1'}
PROJECTS = os.path.join(os.environ.get('CLAUDE_CONFIG_DIR', os.path.expanduser('~/.claude')), 'projects')
FILES = 'abcd'
BATCH = ("In ONE response, call the Bash tool three times in parallel, with exactly these commands: "
         "'sleep 3 && cat a.txt', 'sleep 3 && cat b.txt' and 'sleep 3 && cat c.txt'. After those three results "
         "come back, call Bash once more, on its own, with exactly: 'cat d.txt'. Then reply with exactly: OK")
QUESTION = ("Do not use any tools. Earlier you ran four Bash commands that printed a.txt, b.txt, c.txt and d.txt. "
            "For each one, quote verbatim the first line of its tool result as you see it in this conversation now. "
            "If a result shows an error or is empty, quote that text instead. "
            "Answer as exactly four lines: 'a: ...', 'b: ...', 'c: ...', 'd: ...'.")


def claude(work, prompt, session=None):
    argv = ['claude', '-p', '--model', 'haiku', '--allowedTools', 'Bash(sleep:*)', 'Bash(cat:*)',
            '--settings', json.dumps({'env': ENV}), '--output-format', 'json']
    done = subprocess.run(argv + (['--resume', session] if session else []) + [prompt], cwd=work,
                          env={**os.environ, **ENV}, capture_output=True, text=True, timeout=600)
    try:
        data = json.loads(done.stdout)
    except ValueError:
        data = {'is_error': True, 'result': done.stderr[-400:]}
    if data.get('is_error'):
        print(f'inconclusive: claude -p failed on {prompt[:40]!r}: {data.get("result")}')
        sys.exit(2)
    return data


def transcript(session):
    paths = glob.glob(os.path.join(PROJECTS, '*', f'{session}.jsonl'))
    if not paths:
        print(f'inconclusive: no transcript for session {session} under {PROJECTS}')
        sys.exit(2)
    rows = []
    for line in open(paths[0], errors='replace'):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return paths[0], rows


def blocks(entry, kind):
    message = entry.get('message') if isinstance(entry.get('message'), dict) else {}
    content = message.get('content')
    return [b for b in content if isinstance(b, dict) and b.get('type') == kind] if isinstance(content, list) else []


def unanswered(rows, same_id_merges):
    """Calls with no result in the user entries right after their message, as Claude Code loads a transcript:
    consecutive assistant entries sharing a message id are one message, and so are consecutive user entries.
    With same_id_merges False every assistant entry is its own message, as when each was rebuilt with a fresh id."""
    chat = [e for e in rows if e.get('type') in ('user', 'assistant') and isinstance(e.get('message'), dict)]
    lost, i = [], 0
    while i < len(chat):
        if chat[i]['type'] != 'assistant':
            i += 1
            continue
        mid, calls = chat[i]['message'].get('id'), blocks(chat[i], 'tool_use')
        i += 1
        while same_id_merges and i < len(chat) and chat[i]['type'] == 'assistant' and chat[i]['message'].get('id') == mid:
            calls += blocks(chat[i], 'tool_use')
            i += 1
        answered = set()
        while i < len(chat) and chat[i]['type'] == 'user':
            answered |= {b.get('tool_use_id') for b in blocks(chat[i], 'tool_result')}
            i += 1
        lost += [call.get('id') for call in calls if call.get('id') not in answered]
    return lost


def check(work):
    markers = {name: f'MARKER-{name.upper()}-{secrets.token_hex(4)}' for name in FILES}
    for name, marker in markers.items():
        pad = ''.join(f'padding line {i:04d} for file {name}.txt, nothing to see here\n' for i in range(120))
        with open(os.path.join(work, f'{name}.txt'), 'w') as out:
            out.write(f'{marker}\n{pad}')
    print(f'plugin under test: {PLUGIN}')
    session = claude(work, BATCH)['session_id']
    path, rows = transcript(session)
    if not unanswered(rows, same_id_merges=False):
        print('inconclusive: the model did not write a parallel batch with its results after it; run again')
        return 2
    for turn in range(1, 5):
        claude(work, f'Reply with exactly: filler {turn}', session)
    claude(work, '/compact', session)
    answer = str(claude(work, QUESTION, session).get('result'))
    path, rows = transcript(session)
    boundaries = [i for i, e in enumerate(rows) if e.get('subtype') == 'compact_boundary']
    after = rows[boundaries[-1]:] if boundaries else []
    print(f'transcript: {path}')
    if not any(blocks(e, 'tool_use') for e in after):
        print('inconclusive: the compaction did not run in prune mode; is kiasi loaded with function hooks on?')
        return 2
    lost = unanswered(after, same_id_merges=True)
    missing = [name for name in FILES if markers[name] not in answer]
    print(f'model answer:\n{answer}')
    print(f'calls without a result after compaction: {len(lost)}; markers missing from the answer: {missing or "none"}')
    print('FAIL' if lost or missing else 'PASS')
    return 1 if lost or missing else 0


def main():
    work = tempfile.mkdtemp(prefix='kiasi-e2e-')
    try:
        return check(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
