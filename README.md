# tokenscope

A small observability toolkit for [Claude Code](https://code.claude.com): a compact,
visualized **status line** plus a full-screen **live dashboard** for token usage, cost,
context-window fill, and your subscription rate limits — all from data Claude Code already
emits. No API keys, no telemetry backend, no network calls.

> Status: works, but pre-1.0 — paths and the turn-log schema may still change.

![tokenscope dashboard and status line](assets/tokenscope.png)

## What you get

**1. Status line** (`statusline.sh`) — two compact lines under your prompt:

```
Opus 4.8 · myproject ████░░░░░░ 39% · 78400t · Δ840 tok · Δ$0.02 · $2.41 · 88m 30s
5h ██░░░░░░ 24% ↻1h51m · 7d ███████░ 88% ↻4d3h · rtk ↓90% 3.97M
```

- context-window bar, live tokens, per-turn token/cost deltas, session cost, elapsed
- **5h / 7d rate-limit bars with reset countdowns** — the same data `/usage` shows
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
Claude Code ──stdin──▶ statusline.sh ──▶ usage-snapshot.json ──┐
                              └──▶ turn-log.jsonl               ├─▶ tokenscope.py (dashboard)
~/.claude/projects/**/*.jsonl ─────────────────────────────────┘
```

## Install

Requires `jq` and `python3` (3.8+; standard library only).

```bash
# status line
cp statusline.sh ~/.claude/statusline.sh && chmod +x ~/.claude/statusline.sh
# then add to ~/.claude/settings.json (see settings.example.json):
#   "statusLine": { "type": "command", "command": "~/.claude/statusline.sh" }

# dashboard — run in a second terminal pane
python3 tokenscope.py          # stacked
python3 tokenscope.py -c 2     # two columns
```

Optionally alias it: `alias tokenscope='python3 /path/to/tokenscope.py'`.

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
- The dashboard's own `est` cost uses the rates in `PRICE` at the top of `tokenscope.py` —
  defaults are standard Claude Opus rates; edit them for your plan. The cost-mix proportions
  use those same rates.
- `rate_limits` appear only after the first API turn and only on Pro/Max/Team plans.
- `rtk` integration is optional and degrades silently when `rtk` isn't on `PATH`.

## License

MIT — see [LICENSE](LICENSE).
