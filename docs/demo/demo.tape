# Re-records docs/demo.gif: a real Claude Code session with Kiasi installed, inside a throwaway container laid
# out like a Mac (/Users/dev/my-app) so no real folder names appear. Needs vhs, ttyd, ffmpeg and docker on PATH,
# and docs/demo/env built as docs/development.md describes. Run from the repo root:
#   DEMO=docs/demo/env vhs docs/demo/demo.tape
# The wait for the reply is fast-forwarded and the dashboard frame appended afterwards; see docs/development.md.
Output docs/demo/demo.mp4
Set Shell bash
Set Width 1100
Set Height 620
Set FontSize 14
Set Padding 16
Set Theme "Dracula"
Set Framerate 30
Set TypingSpeed 25ms
Hide
Type "clear; $DEMO/run.sh claude --allowedTools 'Bash(npm ls*)' mcp__kiasi__run"
Enter
Wait+Screen@90s /Try "/
Show
Sleep 1500ms
Type "Run npm ls --all and tell me, in three short sentences, how much of its output reached you and where the rest is."
Sleep 400ms
Enter
Wait+Screen@300s /· done /
Sleep 7s
