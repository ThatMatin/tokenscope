# tokenscope

A small observability toolkit for [Claude Code](https://code.claude.com): a compact,
visualized **status line** plus a unified `tokenscope` CLI for token usage, cost,
context-window fill, and your subscription rate limits — all from data Claude Code already
emits. No API keys, no telemetry backend, no network calls.

> Maintainers: see [RUNBOOK.md](RUNBOOK.md) for architecture, data contracts,
> troubleshooting, and how to extend.

One entrypoint over a shared core (`tokcore.py`):

| command | what it shows |
|---------|---------------|
| `tokenscope live` | full-screen live monitor of the **current** session (default — bare `tokenscope`) |
| `tokenscope grid` | live view of **all open sessions** at once, joined to Claude Code's session registry |
| `tokenscope report` | historical CLI analysis of the turn log (was `tokstats`) |
| `tokenscope dashboard` | self-contained interactive **HTML** dashboard, static `file://` (was `tokstats-dash`) |
| `tokenscope serve` | the same dashboard **live** — a localhost server the page polls so charts + sessions auto-refresh |

Bare `tokenscope` (with the old `-i/-c/-f/--project` flags) still launches `live`, so existing usage keeps working.

> Status: works, but pre-1.0 — paths and the turn-log schema may still change.

![tokenscope dashboard and status line](assets/tokenscope.png)

## What you get

**1. Status line** (`statusline.sh`) — two compact lines under your prompt:

```
Opus 4.8 · myproject ████░░░░░░ 39% · 78400t · Δ840 tok · Δ$0.02 · $2.41 · 88m 30s
5h ██░░░░░░ 24% ↻1h51m · 7d ███████░ 88% ↻4d3h · today 6%/14% · rtk ↓90% 3.97M
```

- context-window bar, live tokens, per-turn token/cost deltas, session cost, elapsed
- **5h / 7d rate-limit bars with reset countdowns** — the same data `/usage` shows
- **today** — how much of an even fair-share day (100%/7 ≈ 14%) of the 7d limit
  you've burned since the first turn today; baseline persists in
  `~/.claude/tokenscope-daily.json`, resets each UTC day or new 7d window
- optional `rtk` token-proxy savings (shown only if an `rtk` CLI is on your `PATH`)

**2. Dashboard** (`tokenscope.py`) — a refreshing full-screen view for a second pane:

```
╭─ tokenscope · Claude Code usage ───────────────── 12:41 ─╮
  session ada2ee06 · myproject · model claude-opus-4-8

  CONTEXT WINDOW            SESSION TOTALS  (340 turns)
    ██████░░░░ 28% 276K/1M    output      308.8K  ← billed driver
                              cache read   43.20M
  TOKEN STATS                 cost (cc)   $24.0451  authoritative
    in:out ratio  149.8 : 1
    cache hit     ████ 94%   RATE
    out/turn      avg 908       output  0 tok/min  last tick: idle
    cost mix      cacheW 46%…   out/turn ▂▁▁▂███▁▁▇▇▁▁
  USAGE / LIMITS (from /usage)
    5h ███████ 59% resets 3h8m  RTK PROXY SAVINGS
    7d █░░░░░░  7% resets 7h18m    saved 3.97M tok (89.9%)
╰──────────────────────────────────────────────────────────╯
```

- **TOKEN STATS** report: cache-hit rate, input:output ratio, per-turn distribution
  (avg/median/max), tokens-per-dollar, cost composition
- authoritative session cost (from the status-line snapshot) vs a local estimate
- **USAGE / LIMITS** — 5h/7d rate limits surfaced from the snapshot
- per-turn output **sparkline**, live output rate
- `-c 2` for a two-column layout

## How it works

Claude Code passes a rich JSON payload to the status-line command on stdin (model, cost,
context window, and — on subscription plans — `rate_limits`). The dashboard, however, gets
no stdin. So the status line writes that payload to `~/.claude/usage-snapshot.json`, and the
dashboard reads it. That snapshot is the **bridge** that gets the `/usage` data and the
authoritative cost into the dashboard. The dashboard also parses the session transcript
(`~/.claude/projects/**/*.jsonl`) directly for the detailed token breakdown and sparkline.

```
Claude Code ──stdin──▶ statusline.sh ──▶ usage-snapshot.json ──────┐
                              ├──▶ turn-log.jsonl                   ├─▶ live / report / dashboard
                              └──▶ tokscope-sessions/{id}.json ──┐  │
~/.claude/projects/**/*.jsonl ────────────────────────────────  ┘  ┘
~/.claude/sessions/{pid}.json  (Claude Code's own registry) ──────▶ grid (joined by sessionId)
```

The **grid** is built by joining Claude Code's own per-process session registry
(`~/.claude/sessions/{pid}.json` — authoritative for which sessions are open, their names, and
liveness) with the per-session payload snapshots the status line writes to
`~/.claude/tokscope-sessions/{sessionId}.json` (authoritative for cost/context). A session with
no snapshot yet still appears (registry-only) until its first turn fills in the numbers.

## Install

Requires `jq` and `python3` (3.8+; standard library only).

```bash
# status line
cp statusline.sh ~/.claude/statusline.sh && chmod +x ~/.claude/statusline.sh
# then add to ~/.claude/settings.json (see settings.example.json):
#   "statusLine": { "type": "command", "command": "~/.claude/statusline.sh" }

# live monitor — run in a second terminal pane
python3 tokenscope.py            # current session, stacked
python3 tokenscope.py -c 2       # two columns
python3 tokenscope.py grid       # all open sessions
python3 tokenscope.py report --days 7
python3 tokenscope.py dashboard  # writes + opens the static HTML dashboard
python3 tokenscope.py serve      # live dashboard at http://127.0.0.1:8765 (auto-refresh)
```

The HTML dashboard charts every field the turn log records: spend per day / cumulative / by
project / **by model**, cost-vs-tokens, the 5-hour rolling window, **cache tokens per day**
(read vs. write — usually the bulk of traffic), and **rate-limit burn over time** (5h / 7d %).

`serve` binds to localhost only (the page carries your usage/cost data) and exposes a `/data`
JSON endpoint the page polls every `-i` seconds; charts and the Active-sessions panel update in
place. Use `dashboard` when you want a shareable static snapshot instead.

Optionally alias it: `alias tokenscope='python3 /path/to/tokenscope.py'`
(and, if you like, `tokstats` / `tokstats-dash` → the `report` / `dashboard` subcommands).

## Notifications & theme

The served dashboard (`tokenscope serve`) places each setting next to the section it
affects: **🔔 Alerts** sits on the **Live** section (it's about sessions wanting you),
**⚙ Chart** sits on the **Spend** section (it tunes the charts), and **Theme** stays
in the header (it's global).

- **🔔 Alerts** (on the Live section) — sounds your machine plays when a session
  wants you. A master on/off, a **volume** slider, plus two events each with its own
  sound (hover the ⓘ for what each is):
  - **Session idle** — a session finished responding and handed control back (the
    `Stop` hook).
  - **Needs your input** — Claude is waiting mid-task on a permission prompt or a
    requested answer (the `Notification` hook).

  Changes are written to `~/.claude/tokenscope-alarm.json`; the `Stop`/`Notification`
  hooks call `notify.sh`, which reads that file and plays the chosen
  `/System/Library/Sounds/*.aiff` at the set volume (`afplay -v`; terminal-bell
  fallback off macOS). Subagent completions deliberately do **not** ring. The static
  `dashboard` export shows the controls view-only (a file:// page can't persist).

- **⚙ Chart** (on the Spend section) — inspection options for precise reading rather
  than at-a-glance shape: **data points** (marker per sample), **exact lines** (disable
  smoothing), **vertical gridlines**, and **drag-to-zoom** (drag across any chart to
  zoom an x-range, double-click to reset; via `chartjs-plugin-zoom`). Remembered in
  `localStorage`.

- **Theme** (header) — Dark / Light / Yellowish, remembered in `localStorage`.

Every **Overview** KPI and **Spend** chart is individually inspectable: click a KPI
card or a chart title and it **highlights as selected** and opens a **detail overlay**
with that entry's live value plus what-it-is / how-to-read / insight / caveats. Only
one is selected at a time; Esc, the ✕, or a backdrop click closes it.

Hook wiring (`~/.claude/settings.json`):

```json
"hooks": {
  "Stop":         [{ "hooks": [{ "type": "command", "command": "~/.claude/tokenscope-notify.sh idle" }] }],
  "Notification": [{ "hooks": [{ "type": "command", "command": "~/.claude/tokenscope-notify.sh needs_input" }] }]
}
```

## The turn log

Each completed turn appends one JSON line to `~/.claude/turn-log.jsonl`:

| field | meaning |
|-------|---------|
| `ts` | UTC timestamp |
| `session`, `project`, `turn` | identity + turn index |
| `turn_tokens`, `turn_cost` | per-turn deltas (context input+output; cost includes subagents) |
| `cum_tokens`, `cum_cost`, `context_pct` | cumulative snapshot |
| `model`, `ctx_window` | model id and window size (200K vs 1M) |
| `cache_read`, `cache_create` | cache token volume — the bulk of traffic, easy to miss |
| `five_h_pct`, `seven_d_pct` | rate-limit burn at that turn |

This is an append-only ledger you can post-process for trends (cost by model, cache-hit over
time, limit burn per session).

## Caveats

- `cost.total_cost_usd` is Claude Code's **client-side estimate**, not your invoice.
- The tools' own `est` cost uses the rates in `PRICE` at the top of `tokcore.py` —
  defaults are standard Claude Opus rates; edit them for your plan. The cost-mix proportions
  use those same rates.
- `rate_limits` appear only after the first API turn and only on Pro/Max/Team plans.
- `rtk` integration is optional and degrades silently when `rtk` isn't on `PATH`.

## License

MIT — see [LICENSE](LICENSE).
