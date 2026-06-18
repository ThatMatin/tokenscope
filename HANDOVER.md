# tokenscope — Session Handover

Continuity note for a new session picking up this work. For the durable
architecture/ops reference see [RUNBOOK.md](RUNBOOK.md); for the feature tour see
[README.md](README.md). This file is the "where things stand right now" snapshot —
delete or overwrite it once you've absorbed it.

_Last updated: 2026-06-18._

## TL;DR

`tokenscope` is a local-only observability toolkit for Claude Code (status line +
`live`/`grid`/`report`/`dashboard`/`serve` CLI over `tokcore.py`). Repo lives at
`~/Projects/tokenscope` (git, local-only, no remote). The live status line is a
**symlink**: `~/.claude/statusline.sh → repo/statusline.sh`. Runtime state is under
`~/.claude/` (never in the repo).

## State of the tree

Clean, all committed (~25 commits, branch `main`, no remote). Recent arc:
- unified CLI + `tokcore.py`; `grid`, `serve`; tokstats folded in (shims at
  `~/.claude/tokstats*.py`).
- status line: `today` daily-slice of the 7d limit; hardened rtk-cache parsing;
  optional personal overlay (`~/.claude/statusline-overlay.sh` — session-topic
  summary, kept out of the repo because it calls the `claude` CLI).
- dashboard IA: **sidebar shell** (`.app` = sticky `.sidebar` + scrolling `.main`).
  Sidebar holds brand, **session search** (`#navSearch` → `SESS_Q`, filters the
  Active-sessions table by name/project), **section nav** (`.navlink` → `id="sec-*"`
  on each `.section`, smooth-scroll + IntersectionObserver scroll-spy), and a foot
  with the **Chart scroll** Zoom/Pan toggle + Theme select (both moved out of the
  old top header; header is now just filters + live badge).
- dashboard chart **navigation modes**: `NAVMODE` (`zoom`|`pan`, persisted
  `ts-navmode`). Zoom = wheel & drag zoom in; Pan = wheel & drag move across a
  zoomed chart. `applyNavMode()` flips the plugin's wheel/drag/pan flags + canvas
  cursor (crosshair vs grab). The zoom plugin only zooms on wheel, so **pan-on-wheel
  is a custom `wheel` listener** that calls `chart.pan({x:-delta})`.
- dashboard chart navigation: global **index-mode hover** (`intersect:false`) so you
  read the value at the nearest x — all series at once — by hovering anywhere, not
  on an invisible radius-0 point; a faint dashed **crosshair** guide at the hovered
  x; and a **reset-zoom button** that appears only while a chart is zoomed/panned.
  Doughnut + scatter charts opt back out to `nearest`/`intersect` per-chart.
- dashboard: notification sounds (idle / needs-input, **never** subagents) with
  master/volume/per-event controls; dark/light/yellowish theme; chart inspection
  options (markers / exact lines / gridlines) + **drag-to-zoom**; per-entry **detail
  overlay** (select → highlight → overlay) now showing the **actual graph** +
  info/insight; adjustable refresh interval; 3-state session pills
  (active/recent/idle) with a parallel-projects summary.

## Hard-won gotchas (read before touching the dashboard)

1. **chartjs-plugin-zoom defaults**: never assign `Chart.defaults.plugins.zoom = {...}`
   — it drops the plugin's `pan`/`limits` keys and throws "Cannot convert undefined
   or null to object", which aborts ALL chart rendering. **Merge** sub-keys instead.
   Doughnut charts have zoom disabled per-chart (no cartesian axes).
2. **Expanded-overlay graph**: do NOT move the live canvas into the overlay — Chart.js
   keeps measuring the original (small) container, so it renders ~300px/thin. The
   overlay builds a **separate** chart instance from `LASTCFG[sel]` (captured in
   `draw()`), created with the overlay as its container, destroyed on close.
3. **Selection highlight** must be **contained** (ring + tint), not a wide glow — a
   big `box-shadow` halo bleeds onto neighbouring cards and reads as "all selected".
4. **Live re-render rebuilds KPIs** (`#kpis` innerHTML each poll) → a `.sel` KPI loses
   its highlight on refresh. Chart cards persist (updated in place). Acceptable today;
   if you want sticky KPI selection across polls, re-apply `.sel` after render.
5. Settings persist only in `serve` mode (POST `/alarm`; localStorage for theme /
   chart-opts / refresh / recent-threshold). The static `dashboard` export is
   view-only for the alarm controls.
6. **Mini bar fills (`.ctxbar`) must be `display:block`, not `inline-block`.** As
   inline-block, the track's `line-height` pushes the fill below the 6px track box
   and `overflow:hidden` on `.ctxtrack` clips it away — every bar then shows only
   the empty gray rail (fill is in the DOM with correct width/colour, just not
   painted; `getComputedStyle` lies, so verify with a screenshot, not the dump).
   Session-table tracks also need the trailing `${ctx}%` in a fixed-width `.ctxpct`
   span — the right-aligned cell otherwise shifts each track's left edge by the
   digit-width of the number, so tracks don't line up. (Fixed 2026-06-18.)
7. **Index-mode hover is global** (`Chart.defaults.interaction/tooltip`). It makes
   line/bar charts navigable but is meaningless on charts without shared x bins —
   doughnut (proj/model) and the scatter must set `interaction:{mode:"nearest",
   intersect:true}` AND the same on their `tooltip`, or hovering highlights every
   slice/point. The crosshair plugin already skips doughnut/pie by type.
8. **Pan-on-wheel is custom, not the zoom plugin.** chartjs-plugin-zoom only zooms
   on wheel; in pan mode a `wheel` listener calls `chart.pan({x:-delta})`. Doughnuts
   set `pan:{enabled:false}` in their per-chart config so the global `NAVMODE` toggle
   (which mutates `Chart.defaults.plugins.zoom.pan.enabled`) never pans them.

## How to verify dashboard changes (USE THIS — don't eyeball)

Headless Chrome catches the JS runtime errors that broke charts twice here:

```bash
cd ~/Projects/tokenscope
python3 -c "import dashboard,tokcore; open('/tmp/d.html','w').write(
  dashboard.build_html(dashboard.load(tokcore.TURN_LOG), dashboard.session_cards(), live=False))"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --no-sandbox --enable-logging=stderr --log-level=0 \
  --virtual-time-budget=5000 --dump-dom "file:///tmp/d.html" 2>/tmp/c.err >/tmp/d.dom
grep -icE 'uncaught|TypeError' /tmp/c.err            # expect 0
grep -cE '<canvas id="c[A-Za-z]+" width="[0-9]{3,}"' /tmp/d.dom   # expect 10
```

For interaction tests, inject a `<script>` before `</body>` that clicks elements and
writes results to `document.title`, then read `<title>` from the dumped DOM (clicks +
same-document state; iframes hit file:// cross-origin limits). Examples used this
turn checked: `.sel` count == 1, expanded `canvas.clientHeight`, canvas restored /
overlay cleaned on close.

Also: `python3 -c "import ast; ast.parse(open('dashboard.py').read())"` and
`node --check` on the extracted inline `<script>` for fast syntax checks.

## Suggested next steps / open ideas (not started)

- **"recently" UX**: currently a hard threshold (default 2 min, adjustable). Offered
  alternatives: opacity fade by age; auto-derive from a multiple of the refresh
  interval. User hasn't chosen.
- **Parallel projects**: could go beyond the chip summary — group the sessions table
  by project, or a per-project lane.
- **Sticky KPI selection** across live polls (see gotcha 4).
- **Enlarged-chart interactions**: the overlay chart has zoom/hover; could add a
  reset-zoom button and per-series toggles in the expanded view.
- `report`/`dashboard` could share a small turn-log query layer if they drift.

## Key files

`statusline.sh` (producer + live line) · `tokcore.py` (shared core) · `dashboard.py`
(HTML/JS — biggest file) · `serve.py` (server + `/alarm`) · `live.py` · `grid.py` ·
`report.py` · `notify.sh` (hook-driven sounds). Runtime: `~/.claude/turn-log.jsonl`,
`usage-snapshot.json`, `tokscope-sessions/`, `tokenscope-alarm.json`,
`tokenscope-daily.json`, `rtk-cache.txt`.
