# Re-records the demo: a real Claude Code session with Kiasi installed, inside a throwaway container laid
# out like a Mac (/Users/dev/my-app) so no real folder names appear. Needs vhs, ttyd, ffmpeg and docker on PATH,
# and docs/demo/env built as docs/development.md describes. Run from the repo root:
#   DEMO=docs/demo/env vhs docs/demo/demo.tape
# Rendered at 2x (2200x1240, 28px) and shown at 1100 CSS px so text stays sharp on high-density screens.
# The wait for the reply is fast-forwarded and the dashboard frame appended afterwards; see docs/development.md.
Output docs/demo/demo.mp4
Set Shell bash
Set Width 2200
Set Height 1240
Set FontSize 28
Set Padding 32
Set Framerate 30
Set TypingSpeed 25ms
# Warm palette matched to the website, with saturated ANSI colors so Claude Code's markers and the status line read clearly.
Set Theme { "name": "kiasi", "background": "#15120f", "foreground": "#efe9dd", "cursor": "#f2b632", "selection": "#3a342c", "black": "#26211c", "red": "#ff6b5b", "green": "#4fd68a", "yellow": "#f2b632", "blue": "#5aa7ff", "magenta": "#d08cff", "cyan": "#3fd3d8", "white": "#d9d2c5", "brightBlack": "#8a8174", "brightRed": "#ff8a7a", "brightGreen": "#7fe8a8", "brightYellow": "#ffd166", "brightBlue": "#86c0ff", "brightMagenta": "#e2b0ff", "brightCyan": "#7fe8ec", "brightWhite": "#ffffff" }
Hide
Type "clear; $DEMO/run.sh claude --allowedTools 'Bash(npm ls*)' mcp__kiasi__run"
Enter
Wait+Screen@90s /auto mode on/
Show
Sleep 1500ms
Type "Run npm ls --all and tell me, in three short sentences, how much of its output reached you and where the rest is."
Sleep 400ms
Enter
Wait+Screen@300s /· done /
Sleep 7s
