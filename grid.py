#!/usr/bin/env python3
"""grid — live view of all open Claude Code sessions at once.

Reads the per-session snapshots statusline.sh writes to ~/.claude/sessions/.
Account-wide 5h/7d limits are shared, so they show once at the top; each session
gets a compact row (name · dir · model · context fill · cost · last active).
"""
import os
import sys
import time
from datetime import datetime

from tokcore import (C, bar, colorbar, fmt_countdown, money, discover_sessions,
                     read_daily, vlen, vpad)


def fmt_age(secs):
    secs = int(secs)
    if secs < 90:
        return f"{secs}s"
    if secs < 5400:
        return f"{secs//60}m"
    return f"{secs//3600}h"


def session_row(s, namew):
    name = (s.get("session_name") or s.get("session_id", "")[:8] or "?")[:namew]
    proj = os.path.basename((s.get("workspace") or {}).get("current_dir", "") or "")[:18]
    age = fmt_age(s.get("_age", 0))
    # ● busy (running a turn) · ◍ recently active · ○ idle.
    if s.get("_status") == "busy":
        live = f"{C['g']}●{C['x']}"
    elif s.get("_age", 1e9) < 120:
        live = f"{C['y']}◍{C['x']}"
    else:
        live = f"{C['dim']}○{C['x']}"
    if not s.get("_has_snapshot"):
        # Registry-only: session exists but hasn't written a snapshot yet.
        ctx = f"{C['dim']}{'(no turn yet)':<19}{C['x']}"
        return (f"  {live} {C['b']}{vpad(name, namew)}{C['x']}  "
                f"{C['dim']}{proj:<18}{C['x']} {'?':<10}  {ctx} {C['dim']}{'—':>7}{C['x']} "
                f"{C['dim']}{'—':>6}{C['x']} {C['dim']}{'—':>9}{C['x']}  "
                f"{C['dim']}{age:>4} ago{C['x']}")
    cw = s.get("context_window") or {}
    pct = cw.get("used_percentage")
    frac = (pct / 100) if pct is not None else 0
    model = (s.get("model") or {}).get("display_name", "?")[:10]
    cost = (s.get("cost") or {}).get("total_cost_usd", 0) or 0
    hit = s.get("cache_hit")
    io = s.get("io_ratio")
    hit_s = f"{hit*100:4.0f}%" if hit is not None else "   ?"
    io_s = f"{io:4.0f}:1" if io else "   ?"
    return (f"  {live} {C['b']}{vpad(name, namew)}{C['x']}  "
            f"{C['dim']}{proj:<18}{C['x']} {model:<10}  "
            f"{colorbar(frac, 12)} {(pct or 0):4.1f}%  "
            f"{C['dim']}hit{C['x']} {hit_s} {C['dim']}io{C['x']} {io_s}  "
            f"{C['g']}{money(cost):>9}{C['x']}  {C['dim']}{age:>4} ago{C['x']}")


def render(sessions, now, refresh, max_age):
    clock = datetime.now().strftime("%H:%M:%S")
    head = f"{C['hdr']}╭─ tokenscope grid · {len(sessions)} open session(s) "
    head += "─" * 18 + f" {clock} ─╮{C['x']}"
    out = [head, ""]

    # Account-wide limits — identical across sessions; take the freshest one that
    # actually carries a snapshot (registry-only sessions have no rate_limits).
    snap = next((s for s in sessions if s.get("rate_limits")), None)
    rl = (snap or {}).get("rate_limits") if snap else None
    if rl:
        parts = []
        for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
            seg = rl.get(key) or {}
            pct = seg.get("used_percentage")
            if pct is None:
                continue
            reset = fmt_countdown(seg.get("resets_at"))
            parts.append(f"{label} {colorbar(pct/100, 10)} {pct:4.1f}% "
                         f"{C['dim']}↻{reset}{C['x']}")
        if parts:
            out.append("  " + "   ".join(parts))
        daily = read_daily(snap, now)
        if daily:
            over = daily["frac"] > 1.0
            note = f"{C['r']}over{C['x']}" if over else f"{C['dim']}of daily limit{C['x']}"
            out.append(f"  today {colorbar(daily['frac'], 10)} {daily['frac']*100:4.0f}% {note}"
                       f"  {C['dim']}(limit {daily['limit']:.1f}%/day){C['x']}")
        out.append("")

    if not sessions:
        scope = f"updated in the last {max_age//60}m" if max_age else "with a live process"
        out.append(f"  {C['dim']}No Claude Code sessions {scope}.{C['x']}")
    else:
        namew = min(28, max(10, max(len(s.get("session_name") or "") for s in sessions)))
        out.append(f"  {C['dim']}  {'session':<{namew}}  {'project':<18} {'model':<10}  "
                   f"{'context':<19} {'cache/io':<13} {'cost':>9}{C['x']}")
        for s in sessions:
            out.append(session_row(s, namew))
        tot = sum((x.get("cost") or {}).get("total_cost_usd", 0) or 0 for x in sessions)
        out.append("")
        out.append(f"  {C['dim']}total across open sessions: {C['x']}{C['g']}{money(tot)}{C['x']}")

    out.append("")
    scope = f"updated <{max_age//60}m" if max_age else "all live processes"
    out.append(f"{C['dim']}  Ctrl-C to quit · refresh {refresh}s · "
               f"{C['g']}●{C['dim']} busy · {C['y']}◍{C['dim']} recent · ○ idle · "
               f"{scope}{C['x']}")
    return "\n".join(out)


def run(args):
    refresh = args.interval
    max_age = args.window
    try:
        while True:
            now = time.time()
            sessions = discover_sessions(max_age=max_age)
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(render(sessions, now, refresh, max_age) + "\n")
            sys.stdout.flush()
            time.sleep(refresh)
    except KeyboardInterrupt:
        sys.stdout.write("\033[0m\n")
        print("grid stopped.")
