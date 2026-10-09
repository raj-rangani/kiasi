# Re-records docs/demo.gif. Needs vhs, ttyd and ffmpeg on PATH, a project with node_modules in $PROJECT,
# and docs/demo/cut.txt from make-cut.py. Run from the repo root: KIASI=$PWD PROJECT=~/app vhs docs/demo/demo.tape
# The dashboard frame is appended afterwards; see docs/development.md.
Output docs/demo/demo.mp4
Set Shell bash
Set Width 1100
Set Height 620
Set FontSize 15
Set Padding 18
Set Framerate 30
Set TypingSpeed 28ms
Hide
Type "cd $PROJECT && clear"
Enter
Show
Sleep 500ms
Type "npm ls --all"
Sleep 300ms
Enter
Sleep 1800ms
Type "# 116,179 chars, about 29k tokens. Without Kiasi all of it enters the context"
Enter
Type "# and is re-sent on every later step of the session."
Enter
Sleep 2400ms
Hide
Type "clear"
Enter
Show
Type "# With Kiasi, this is what Claude sees instead:"
Enter
Hide
Type "python3 $KIASI/docs/demo/show-cut.py $KIASI/docs/demo/cut.txt"
Enter
Show
Sleep 3400ms
Type "# 2,940 chars entered the context. The full output is on disk, searchable."
Enter
Sleep 2600ms
