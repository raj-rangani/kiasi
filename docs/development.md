# Developing Kiasi

```
python3 -m unittest discover -s tests   # hook handlers, reports, dashboard
claude plugin test .                     # hooks/*.test.ts on the real engine
claude plugin validate .                 # manifest, marketplace and hooks.json
```

`scripts/kiasi.py` is the hook entry point and dispatches to `scripts/core/`. Reports live in `scripts/reports/`, the dashboard in `dashboard/`, and `scripts/README.md` says which hook calls what.

## Re-recording the demo

`docs/demo.mp4` (the website hero) and `docs/demo.gif` (the README, since GitHub does not play repo videos) are one real Claude Code session recorded with [VHS](https://github.com/charmbracelet/vhs), with the wait for the reply played at 12× and the dashboard overview appended. The tape renders at 2× (2200×1240, 28 px) and the page shows it at 1100 CSS px, so text stays sharp on high-density screens; the palette is a warm one matched to the website, set in the tape. It runs inside a throwaway Docker container laid out like a Mac, so the only paths on screen are `/Users/dev/my-app` and `/Users/dev/.claude/...`, never your own folder names. The kit is `docs/demo/env/`: a `Dockerfile` with Node, Python and the `claude` CLI, and `run.sh`, which mounts a demo home, a demo project, this checkout as the plugin, and your real `~/.claude/.credentials.json` read from its own place (it is never copied).

Needs `vhs`, `ttyd`, `ffmpeg` and `docker` on PATH, and `claude` logged in on the host. Set up once:

```
docker build -t kiasi-demo docs/demo/env
mkdir -p docs/demo/env/home/.claude/kiasi docs/demo/env/home/.claude/plugins/data docs/demo/env/home/.claude/plugins/kiasi docs/demo/env/my-app
cp scripts/statusline.py docs/demo/env/home/.claude/kiasi/
# home/.claude/settings.json: the hooks and statusLine blocks the plugin's setup writes, with the statusLine command pointing at /Users/dev/.claude/kiasi/statusline.py
# home/.claude.json: {"hasCompletedOnboarding": true, "theme": "dark"} so Claude Code skips its first-run screens
# my-app/package.json: any real project's manifest with "name": "my-app" and nothing identifying; then git init -b main and one commit in my-app
```

Point `DEMO_NODE_MODULES` at a real `node_modules` so `npm ls --all` has a tree to print. `home*/` and `my-app/` under `docs/demo/env/` are gitignored. Then, from the repo root:

```
DEMO=docs/demo/env DEMO_NODE_MODULES=/path/to/a/node/project/node_modules vhs docs/demo/demo.tape
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 docs/demo/demo.mp4); B=$(python3 -c "print($D-8.5)")
ffmpeg -i docs/demo/demo.mp4 -filter_complex "[0:v]trim=0:5.5,setpts=PTS-STARTPTS[a];[0:v]trim=5.5:$B,setpts=(PTS-STARTPTS)/12[b];[0:v]trim=$B,setpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=1:a=0[v]" -map "[v]" cut.mp4
google-chrome --headless=new --screenshot=overview.png --window-size=1400,1100 --force-device-scale-factor=2 --virtual-time-budget=6000 --force-prefers-reduced-motion http://127.0.0.1:8787/#overview
ffmpeg -loop 1 -t 3.2 -i overview.png -vf "scale=2200:-2,crop=2200:1240:0:0,format=yuv420p" -r 30 dash.mp4
ffmpeg -i cut.mp4 -i dash.mp4 -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]" -map "[v]" -c:v libx264 -preset slow -crf 21 -r 30 -movflags +faststart docs/demo.mp4
ffmpeg -ss 0.5 -i docs/demo.mp4 -frames:v 1 -vf scale=1100:-2 -q:v 4 docs/demo-poster.jpg
ffmpeg -i docs/demo.mp4 -filter_complex "scale=1100:-2:flags=lanczos,fps=15,split[a][b];[a]palettegen=max_colors=256:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" -loop 0 docs/demo.gif
```

The reply is whatever that run produces, so look at the frames before committing and update the alt text in `README.md` and `docs/index.html` if the numbers changed. The session's Kiasi data lands in `docs/demo/env/home/.claude/plugins/data/`, not in yours. Do not commit `docs/demo/demo.mp4`, the raw render; `docs/demo.mp4` is the cut one.

### Dashboard video

The website's dashboard tile plays `docs/dash-dark.mp4` or `docs/dash-light.mp4` to match the page theme. Both are recorded from the running dashboard with Playwright, at 2200x1240 so they stay sharp on dense screens. Real project names and paths are rewritten to generic ones before anything is drawn; check the `RULES` list in the script covers yours.

```bash
python3 scripts/dashboard.py 8787 &      # the dashboard the recording walks through
NODE_PATH=$(npm root -g) node docs/demo/record-dashboard.mjs light   # needs playwright with chromium
NODE_PATH=$(npm root -g) node docs/demo/record-dashboard.mjs dark
for t in light dark; do
  ffmpeg -y -ss 0.6 -i dash-$t.webm -an -vf "fps=30,format=yuv420p" -c:v libx264 -preset slow -crf 24 -movflags +faststart docs/dash-$t.mp4
  ffmpeg -y -ss 1.2 -i docs/dash-$t.mp4 -frames:v 1 -q:v 4 docs/dash-$t-poster.jpg
done
```
