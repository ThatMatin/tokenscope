#!/usr/bin/env python3
"""report — historical CLI analysis of ~/.claude/turn-log.jsonl (was tokstats).

Each log line is one completed turn: {ts, session, project, turn, turn_tokens,
turn_cost, cum_tokens, cum_cost, context_pct, ...}. turn_tokens is main-loop
context delta (excludes subagents, can go negative on compaction); turn_cost
includes subagent spend.
"""
import sys
from collections import defaultdict

from tokcore import bar, load_turnlog, median, money, peak_window, toks


def run(args):
    rows = load_turnlog(args.log, args.days, args.project)
    if not rows:
        sys.exit("No turns match the filter (log fills as you use Claude Code).")

    total_cost = sum(r.get("turn_cost", 0) or 0 for r in rows)
    pos_tok = sum(t for r in rows if (t := r.get("turn_tokens", 0) or 0) > 0)
    costs = [r.get("turn_cost", 0) or 0 for r in rows]
    sessions = {r.get("session") for r in rows}
    d0, d1 = min(r["_dt"] for r in rows), max(r["_dt"] for r in rows)
    ndays = max(1, (d1.date() - d0.date()).days + 1)
    compacts = sum(1 for r in rows if (r.get("turn_tokens", 0) or 0) < 0)

    W = 60
    print("═" * W)
    print(f" tokenscope report — {len(rows)} turns · {len(sessions)} sessions"
          + (f" · last {args.days}d" if args.days else "")
          + (f" · {args.project}" if args.project else ""))
    print(f" {d0:%Y-%m-%d %H:%M} → {d1:%Y-%m-%d %H:%M}  ({ndays} day{'s' if ndays>1 else ''})")
    print("═" * W)
    print(f"  Total spend         {money(total_cost)}")
    print(f"  Turn tokens (added) {toks(pos_tok)}   (main-loop only, excl. subagents)")
    print(f"  Cost / turn         avg {money(total_cost/len(rows))} · median {money(median(costs))} · max {money(max(costs))}")
    print(f"  Spend / day         {money(total_cost/ndays)}")
    if compacts:
        print(f"  Context compactions {compacts} turn(s) shrank context (negative token delta)")

    pk, pk_end = peak_window(rows, 5)
    if pk_end:
        print(f"  Peak 5h window      {money(pk)}  (ending {pk_end:%Y-%m-%d %H:%M})")
        print(f"                      → your heaviest 5-hour burn so far")

    by_day = defaultdict(lambda: [0.0, 0, 0])
    for r in rows:
        d = by_day[r["_dt"].strftime("%Y-%m-%d %a")]
        d[0] += r.get("turn_cost", 0) or 0
        d[1] += 1
        d[2] += max(0, r.get("turn_tokens", 0) or 0)
    day_max = max(v[0] for v in by_day.values()) or 1
    print("\n By day")
    print(" " + "─" * (W - 1))
    for day in sorted(by_day):
        c, t, tk = by_day[day]
        print(f"  {day}  {money(c):>9}  {bar(c/day_max)}  {t:>3} turns · {toks(tk)}")

    by_proj = defaultdict(lambda: [0.0, 0])
    for r in rows:
        p = by_proj[r.get("project", "?")]
        p[0] += r.get("turn_cost", 0) or 0
        p[1] += 1
    if len(by_proj) > 1:
        proj_max = max(v[0] for v in by_proj.values()) or 1
        print("\n By project")
        print(" " + "─" * (W - 1))
        for p, (c, t) in sorted(by_proj.items(), key=lambda kv: -kv[1][0]):
            print(f"  {p[:20]:<20}  {money(c):>9}  {bar(c/proj_max)}  {t:>3} turns")

    print(f"\n Top {args.top} most expensive turns")
    print(" " + "─" * (W - 1))
    top = sorted(rows, key=lambda r: -(r.get("turn_cost", 0) or 0))[:args.top]
    for r in top:
        print(f"  {r['_dt']:%m-%d %H:%M}  {money(r.get('turn_cost',0)):>9}  "
              f"{toks(r.get('turn_tokens',0)):>7} tok  "
              f"ctx {r.get('context_pct','?'):>2}%  {r.get('project','?')[:18]}")
    print("═" * W)
