# tokenscope — Runbook

Operational guide to the whole project: what each piece is, the invariants you must not
break, how data flows, how to extend and troubleshoot it, and why it's shaped the way it
is. For the user-facing feature tour and install see [README.md](README.md); this is the
maintainer's map.

---

## 1. What this is

tokenscope turns the data Claude Code already emits — the status-line payload and
the session transcripts — into **token / cost / context / rate-limit observability**,
with **no API keys, no telemetry backend, and no network calls** (the one opt-in
exception: a personal status-line overlay that summarizes a session via the `claude`
CLI; kept out of the repo). Two surfaces:

- a **status line** (`statusline.sh`) under your prompt, and
- a **dashboard / monitors** (`tokenscope.py` → `live`, `grid`, `report`,
  `dashboard`, `serve`).

---

## 2. Invariants — do not break these

These are load-bearing. A change that violates one is wrong even if it works. (The
[History](#9-history--design-decisions) section explains how each was learned.)

- **Local-only, no network.** No API keys, no telemetry, no outbound calls from anything
  in the repo. The single exception is Chart.js from a CDN (chart *data* is embedded; only
  the library is fetched). If a feature needs the network or spends tokens, it goes in the
  **personal overlay** (sourced at the end of `statusline.sh`), never in the repo.
- **Honesty about data quality.** Status-line/dashboard values are colored by trust tier:
  *exact* (from the harness), *partial* (e.g. token counts that exclude subagents),
  *borderline* (exact value / heuristic slicing), *generated* (a model summary). Any new
  value must pick the correct tier; the dashboard legend mirrors this. Never present a
  heuristic as exact.
- **Repo is the single source of truth; runtime state lives in `~/.claude/`.** Install by
  **symlink** (§7) so the live `statusline.sh` can't drift ahead of the repo. Never edit
  the deployed copy directly.
- **Subagent completions never ring.** Notifications fire on `Stop` (idle) and
  `Notification` (needs input) only — deliberately not on subagent finish.

---

## 3. Architecture & data flow

```
Claude Code ──stdin (JSON payload)──▶ statusline.sh
                                         │
              ┌──────────────────────────┼───────────────────────────┐
              ▼                          ▼                            ▼
   ~/.claude/usage-snapshot.json   ~/.claude/turn-log.jsonl   ~/.claude/tokscope-sessions/<id>.json
   (latest payload: /usage,         (append-only ledger,        (per-session latest payload,
    cost, context — for the          one line per completed       for the `grid` multi-session view)
    dashboard which has no stdin)     turn)
              │                          │                            │
              └──────────────┬───────────┴───────────────┬───────────┘
                             ▼                            ▼
                   tokenscope.py live/grid       tokenscope.py report/dashboard/serve
                   (read snapshots + transcripts) (read the turn-log; serve also reads snapshots)

Claude Code hooks ──▶ notify.sh <event>  ──reads──▶ ~/.claude/tokenscope-alarm.json
  (Stop / Notification)                              (written by `serve`'s /alarm endpoint)
```

Everything is local files under `~/.claude/`. The repo is the source of truth for
code; runtime state lives in `~/.claude/` (never in the repo).

---

## 4. Components (every file in the repo)

| File | Role |
|------|------|
| `statusline.sh` | The data **producer** + the live two-line status display. Reads the harness JSON on stdin; writes `usage-snapshot.json`, appends to `turn-log.jsonl`, and writes per-session snapshots. Renders: model · dir · context bar · tokens (Δ/turn) · cost (Δ/turn) · elapsed; then 5h/7d limits + `today` daily slice + optional `rtk`. Sources an optional personal overlay at the end. |
| `tokenscope.py` | CLI dispatcher. Subcommands: `live`, `grid`, `report`, `dashboard`, `serve`. Bare invocation → `live` (back-compat). |
| `tokcore.py` | Shared core: paths (`TURN_LOG`, snapshot dirs), turn-log loader, transcript parsing, pricing (`PRICE`), formatting helpers. Imported by every consumer. |
| `live.py` | Live single-session TUI monitor (sparkline, rates, `-c 2` columns). |
| `grid.py` | Live TUI of **all** open sessions, joined from the session registry + per-session snapshots. |
| `report.py` | Historical CLI analysis of the turn-log (totals, per-day, per-project, peak 5h window, top turns). |
| `dashboard.py` | Builds the self-contained interactive HTML (Chart.js). Used as a static `file://` export and as the page `serve` returns. |
| `serve.py` | Localhost HTTP server: serves the dashboard, a `/data` poll endpoint, and `/alarm` GET/POST for the notification config. |
| `notify.sh` | Plays a per-event notification sound, driven by `~/.claude/tokenscope-alarm.json`. Invoked by Claude Code `Stop`/`Notification` hooks. |
| `settings.example.json` | Example `statusLine` wiring for `~/.claude/settings.json`. |
| `assets/` | Screenshot(s) for the README. |

---

## 5. Data contracts

### `~/.claude/turn-log.jsonl` (append-only, one line per completed turn)

| field | meaning |
|-------|---------|
| `ts` | UTC timestamp |
| `session`, `project`, `turn` | identity + turn index |
| `turn_tokens` | main-loop context delta (input+output; **excludes** subagents + cache; can be negative on compaction) |
| `turn_cost` | per-turn cost delta (**includes** subagent spend) |
| `cum_tokens`, `cum_cost`, `context_pct` | cumulative snapshot at turn end |
| `model`, `ctx_window` | model id + window size (200K vs 1M) |
| `cache_read`, `cache_create` | cache token volume — usually the bulk of traffic |
| `five_h_pct`, `seven_d_pct` | rate-limit burn at that turn |

### `~/.claude/usage-snapshot.json`
The full, latest status-line payload. The bridge that gets `/usage` rate limits and
authoritative cost into the dashboard (which has no stdin).

### `~/.claude/tokscope-sessions/<session_id>.json`
Per-session latest payload, for the `grid` multi-session view.

### `~/.claude/tokenscope-alarm.json` (notification config)
```json
{ "master": true, "volume": 0.8,
  "events": { "idle":        {"enabled": true, "sound": "Glass"},
              "needs_input": {"enabled": true, "sound": "Ping"} } }
```
Written by `serve`'s `/alarm` POST (sanitized server-side); read by `notify.sh`.

### Status-line state (siblings of the transcript, `${transcript%.jsonl}.*`)
`.tokdelta` (per-turn baseline), `.topic` / `.topic.lines` (overlay summary cache).
Plus `~/.claude/tokenscope-daily.json` (the `today` daily-slice baseline) and
`~/.claude/rtk-cache.txt` (cached rtk savings).

---

## 6. Subcommand reference

| Command | What it does | Key flags |
|---------|--------------|-----------|
| `tokenscope live` | live single-session TUI | `-i` interval, `-c {1,2}` columns, `-f` pin transcript, `--project` |
| `tokenscope grid` | live TUI of all open sessions | `-i` interval, `-w` window |
| `tokenscope report` | historical turn-log analysis | `--days N`, `--project X`, `--top N`, `--log PATH` |
| `tokenscope dashboard` | write a static HTML file & open it | `--out PATH`, `--no-open`, `--log PATH` |
| `tokenscope serve` | live HTML + `/data` + `/alarm` server (localhost) | `--port` (8765), `--host`, `-i` poll, `--no-open` |

Back-compat: bare `tokenscope` → `live`. `~/.claude/tokstats*.py` are shims → `report`/`dashboard`.

---

## 7. Extend

Where to make a change of each kind, and the invariants (§2) it must respect:

- **New turn-log field:** add it in `statusline.sh` (the `printf` that appends the
  record) and read it in `tokcore.py`. Keep the schema in §5 in sync.
- **New status-line segment:** add to `statusline.sh`; if it spends tokens or needs
  the network, put it in the personal overlay, not the repo (preserve the no-network
  contract).
- **New notification event:** add a hook → `notify.sh <event>`, extend the config
  schema + `serve.py` `write_alarm` whitelist + the dashboard Alerts panel.
- **New chart/metric:** add to `dashboard.py`; reuse the shared `GRID` object and
  `Chart.defaults` so global chart-options (markers/exact/grid) and the navigation
  layer apply for free. A cartesian chart automatically gets index-mode hover, the
  crosshair, a per-graph **Zoom/Pan toggle + reset toolbar** (`initChartToolbars`),
  and the `NAVMODE` wheel/drag behaviour. Doughnut/pie charts must opt out per-chart
  (`interaction:{mode:"nearest",intersect:true}` + `tooltip` same + `pan:{enabled:false}`).
- **New nav section:** give the `.section` an `id="sec-*"` and add a matching
  `.navlink` (with `data-target`) to the sidebar `nav` — scroll-spy and the search
  "Go to section" jump pick it up automatically.

---

## 8. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Status line blank in a new session | Claude Code renders it after the first interaction; also confirm `settings.json` `statusLine.command`. |
| Per-turn Δ stuck at 0 | The `.tokdelta` baseline keys off `transcript_path`; a stale state file or a transcript move resets it. |
| `rtk ↓% <n>` malformed / arithmetic error | A legacy `rtk-cache.txt` missing its timestamp field. The reader now validates it and self-heals on the next refresh. |
| Dashboard charts blank | Chart.js CDN unreachable (the only network dependency); data is embedded, so check connectivity. |
| No notification sound | Check `~/.claude/tokenscope-alarm.json` (`master`/event `enabled`/`volume`>0/`sound`≠`none`); hooks load at session start; sandbox/SSH may lack an audio device. |
| Alarm controls do nothing in the static export | By design — a `file://` page can't persist; use `tokenscope serve`. |
| Rate-limit (5h/7d/today) missing | `rate_limits` appear only after the first API turn, on Pro/Max/Team plans. |

---

## 9. Install (maintainer)

The [README](README.md#60-second-quickstart) has the user quickstart (it uses `cp`).
Maintainers **symlink** instead, so the repo stays the single source of truth and the
live copy can't drift (invariant §2):

```bash
# Status line — symlink, don't copy:
ln -sf "$PWD/statusline.sh" ~/.claude/statusline.sh
# settings.json: "statusLine": { "type": "command", "command": "~/.claude/statusline.sh" }

# Notifications (optional) — symlink the helper, wire the hooks:
ln -sf "$PWD/notify.sh" ~/.claude/tokenscope-notify.sh
#   "Stop":         [{ "hooks": [{ "type":"command","command":"~/.claude/tokenscope-notify.sh idle" }] }]
#   "Notification": [{ "hooks": [{ "type":"command","command":"~/.claude/tokenscope-notify.sh needs_input" }] }]
```

Requires `jq` and `python3` (3.8+, stdlib only). macOS for sound (`afplay`); the status
line works anywhere. Settings changes are read at session start — restart Claude Code to
pick up new hooks.

---

## 10. History & design decisions

The project grew from a single status-line tweak into a toolkit; the arc below is *why*
the invariants in §2 exist:

1. **Status line, then a delta gauge.** Added per-turn token/cost deltas to a
   cumulative status line — which exposed that tool results log as `type:user`,
   handled by excluding `tool_result` lines from the turn-boundary count.
2. **A turn log.** The status line began appending one record per turn
   (`turn-log.jsonl`) — the ledger everything analytical reads.
3. **Ground-truthness coloring.** Status-line values are colored by trust: exact
   (harness), partial (token counts exclude subagents), borderline (exact value /
   heuristic slicing), generated (model summary). This honesty rule is load-bearing
   across the UI (the dashboard legend mirrors it) → invariant §2.
4. **tokstats → folded into tokenscope.** A separate report/dashboard pair was
   absorbed into this repo as `report.py`/`dashboard.py` over a shared `tokcore.py`;
   the old scripts became back-compat shims. `grid` and `serve` were added.
5. **Convergence + symlink.** The live `~/.claude/statusline.sh` had drifted ahead of
   the repo; it was reconciled and **symlinked** to the repo so it can't drift again
   → invariant §2. Personal extras (topic summary) moved to an optional sourced overlay.
6. **Notifications + theme + inspection.** Sound notifications (idle / needs-input,
   never subagents) with master/volume/per-event controls; dark/light/yellowish
   theme; per-entry guides and chart inspection options.
7. **Sidebar IA + chart navigation.** The dashboard moved from a top-rail header to
   a sidebar shell (brand · search · section nav with scroll-spy · theme). Search
   filters the sessions table live and offers section/project jumps. Charts gained
   index-mode hover, a crosshair, and two navigation modes (Zoom/Pan) controlled by
   a per-graph toolbar and a sidebar toggle — wheel zooms or pans accordingly.
