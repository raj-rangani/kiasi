# Re-records docs/demo.gif: a real Claude Code session with Kiasi installed, in a project that has node_modules.
# Needs vhs, ttyd and ffmpeg on PATH and the claude CLI logged in. Run from the repo root:
#   PROJECT=/path/to/a/node/project vhs docs/demo/demo.tape
# The dashboard frame is appended and the thinking pause is fast-forwarded afterwards; see docs/development.md.
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
Type "unset CLAUDE_CODE_CHILD_SESSION CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_SESSION_ID; cd $PROJECT && clear"
Enter
Type "claude --allowedTools 'Bash(npm ls*)'"
Enter
Wait+Screen@60s /Try "/
Show
Sleep 1500ms
Type "Run npm ls --all. How much of its output reached you, and where is the rest?"
Sleep 400ms
Enter
Wait+Screen@300s /· done /
Sleep 7s
