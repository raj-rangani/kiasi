# Developing Kiasi

```
python3 -m unittest discover -s tests   # hook handlers, reports, dashboard
claude plugin test .                     # hooks/*.test.ts on the real engine
claude plugin validate .                 # manifest, marketplace and hooks.json
```

`scripts/kiasi.py` is the hook entry point and dispatches to `scripts/core/`. Reports live in `scripts/reports/`, the dashboard in `dashboard/`, and `scripts/README.md` says which hook calls what.

## Re-recording the demo

`docs/demo.gif` is a real terminal recording made with [VHS](https://github.com/charmbracelet/vhs), with the dashboard overview appended. The numbers typed in the tape are the ones that run produced; change them if your run differs.

```
npm ls --all 2>&1 | python3 docs/demo/make-cut.py        # in a project with node_modules; writes docs/demo/cut.txt
KIASI=$PWD PROJECT=/path/to/that/project vhs docs/demo/demo.tape
google-chrome --headless=new --screenshot=overview.png --window-size=1400,1100 --virtual-time-budget=6000 --force-prefers-reduced-motion http://127.0.0.1:8787/#overview
ffmpeg -loop 1 -t 3.2 -i overview.png -vf "scale=1100:-2,crop=1100:620:0:0,format=yuv420p" -r 30 dash.mp4
ffmpeg -i docs/demo/demo.mp4 -i dash.mp4 -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0,fps=12,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" docs/demo.gif
```

Afterwards delete the demo run's file from the Kiasi outputs dir and its `"session_id": "demo"` line from `kiasi.jsonl`, and do not commit `cut.txt` or `demo.mp4`.
