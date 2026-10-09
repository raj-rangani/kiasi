# Developing Kiasi

```
python3 -m unittest discover -s tests   # hook handlers, reports, dashboard
claude plugin test .                     # hooks/*.test.ts on the real engine
claude plugin validate .                 # manifest, marketplace and hooks.json
```

`scripts/kiasi.py` is the hook entry point and dispatches to `scripts/core/`. Reports live in `scripts/reports/`, the dashboard in `dashboard/`, and `scripts/README.md` says which hook calls what.

## Re-recording the demo

`docs/demo.gif` is a real Claude Code session recorded with [VHS](https://github.com/charmbracelet/vhs), with the wait for the reply played at 12× and the dashboard overview appended. Re-record it in a project that has `node_modules`, with the plugin installed and `claude` logged in. The reply is whatever that run produces, so look at the frames before committing. The session is a real one and lands in your Kiasi data like any other.

```
PROJECT=/path/to/a/node/project vhs docs/demo/demo.tape
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 docs/demo/demo.mp4); B=$(python3 -c "print($D-8.5)")
ffmpeg -i docs/demo/demo.mp4 -filter_complex "[0:v]trim=0:5.5,setpts=PTS-STARTPTS[a];[0:v]trim=5.5:$B,setpts=(PTS-STARTPTS)/12[b];[0:v]trim=$B,setpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=1:a=0[v]" -map "[v]" cut.mp4
google-chrome --headless=new --screenshot=overview.png --window-size=1400,1100 --virtual-time-budget=6000 --force-prefers-reduced-motion http://127.0.0.1:8787/#overview
ffmpeg -loop 1 -t 3.2 -i overview.png -vf "scale=1100:-2,crop=1100:620:0:0,format=yuv420p" -r 30 dash.mp4
ffmpeg -i cut.mp4 -i dash.mp4 -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0,fps=12,split[a][b];[a]palettegen=max_colors=200:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" -loop 0 docs/demo.gif
```

Do not commit `demo.mp4`.
