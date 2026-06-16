#!/usr/bin/env python3
"""live — single-session live token monitor (the original tokenscope view)."""
import glob
import os
import sys
import time
from datetime import datetime

from tokcore import (C, CONTEXT_WINDOW, PRICE, PROJECTS, bar, colorbar, fmt,
                     fmt_countdown, hms, median, read_daily, read_rtk,
                     read_session, read_snapshot, sparkline, vlen, vpad)


def pick_transcript(pinned, project):
    if pinned:
        return pinned
    if project:
        files = glob.glob(os.path.join(project, "**", "*.jsonl"), recursive=True)
    else:
        files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def section(title, body):
    return [f"  {C['b']}{title}{C['x']}"] + body


def build_sections(path, stats, rtk, snap, run_start, baseline_out, prev_out, now):
    t = stats["totals"]
    elapsed_run = now - run_start
    live_rate = (t["output"] - baseline_out) / (elapsed_run / 60) if elapsed_run > 1 else 0
    tick_delta = t["output"] - prev_out
    sess_elapsed = ((stats["last_ts"] - stats["first_ts"]).total_seconds()
                    if stats["first_ts"] and stats["last_ts"] else 0)

    cw = (snap or {}).get("context_window") or {}
    win = cw.get("context_window_size") or CONTEXT_WINDOW
    if cw.get("used_percentage") is not None:
        ctx_tok = (cw.get("total_input_tokens", 0) or 0) + (cw.get("total_output_tokens", 0) or 0)
        ctx_frac = cw["used_percentage"] / 100
    else:
        ctx_tok = stats["ctx"]
        ctx_frac = ctx_tok / win

    cc_cost = (snap.get("cost") or {}).get("total_cost_usd") if snap else None
    secs = []

    secs.append(section("CONTEXT WINDOW", [
        f"    {colorbar(ctx_frac)} {ctx_frac*100:4.1f}%   {fmt(ctx_tok)} / {fmt(win)}",
    ]))

    body = [
        f"    output         {fmt(t['output']):>10}   {C['dim']}← billed driver{C['x']}",
        f"    input (fresh)  {fmt(t['input']):>10}",
        f"    cache read     {fmt(t['cache_read']):>10}",
        f"    cache write    {fmt(t['cache_5m']+t['cache_1h']):>10}   "
        f"{C['dim']}(5m {fmt(t['cache_5m'])} · 1h {fmt(t['cache_1h'])}){C['x']}",
    ]
    if cc_cost is not None:
        body.append(f"    {C['b']}cost (cc){C['x']}      {C['g']}${cc_cost:>9.4f}{C['x']}   "
                    f"{C['dim']}authoritative · est ${stats['cost']:.2f}{C['x']}")
    else:
        body.append(f"    {C['b']}est. cost{C['x']}      {C['g']}${stats['cost']:>9.4f}{C['x']}   "
                    f"{C['dim']}edit PRICE[] to adjust{C['x']}")
    secs.append(section(f"SESSION TOTALS  ({stats['turns']} turns)", body))

    in_side = t["input"] + t["cache_read"] + t["cache_5m"] + t["cache_1h"]
    cache_hit = t["cache_read"] / in_side if in_side else 0
    grand = in_side + t["output"]
    ratio = (in_side / t["output"]) if t["output"] else 0
    series = stats.get("out_series", [])
    avg_o = sum(series) / len(series) if series else 0
    cost_parts = {
        "out": t["output"] * PRICE["output"] / 1e6,
        "cacheR": t["cache_read"] * PRICE["cache_read"] / 1e6,
        "in": t["input"] * PRICE["input"] / 1e6,
        "cacheW": (t["cache_5m"] * PRICE["cache_5m"] + t["cache_1h"] * PRICE["cache_1h"]) / 1e6,
    }
    ctot = sum(cost_parts.values()) or 1
    mix = " · ".join(f"{k} {v/ctot*100:.0f}%" for k, v in
                     sorted(cost_parts.items(), key=lambda kv: -kv[1]))
    bill = cc_cost if cc_cost else stats["cost"]
    tpd = grand / bill if bill else 0
    secs.append(section("TOKEN STATS", [
        f"    in:out ratio   {ratio:5.1f} : 1   {C['dim']}(in-side vs output){C['x']}",
        f"    cache hit      {colorbar(cache_hit, 12)} {cache_hit*100:4.1f}%",
        f"    out/turn       avg {fmt(avg_o)} · med {fmt(median(series))} · max {fmt(max(series) if series else 0)}",
        f"    total tokens   {fmt(grand)}   {C['dim']}{fmt(tpd)}/${C['x']}",
        f"    cost mix       {C['dim']}{mix}{C['x']}",
    ]))

    arrow = f"{C['g']}+{tick_delta}{C['x']}" if tick_delta else f"{C['dim']}idle{C['x']}"
    rbody = [
        f"    output    {live_rate:6.0f} tok/min   last tick: {arrow}",
        f"    span      {hms(sess_elapsed)}   monitor {hms(elapsed_run)}",
    ]
    spark = sparkline(series, 40)
    if spark:
        rbody.append(f"    out/turn  {C['g']}{spark}{C['x']}")
    secs.append(section("RATE", rbody))

    rl = (snap or {}).get("rate_limits") if snap else None
    if rl:
        stale = f" {C['dim']}({int(snap['_age'])}s old){C['x']}" if snap.get("_age", 0) > 30 else ""
        ubody = []
        for key, label in (("five_hour", "5h "), ("seven_day", "7d ")):
            seg = rl.get(key) or {}
            pct = seg.get("used_percentage")
            if pct is None:
                continue
            reset = fmt_countdown(seg.get("resets_at"))
            rtxt = f"{C['dim']}resets {reset}{C['x']}" if reset else ""
            ubody.append(f"    {label} {colorbar(pct/100, 12)} {pct:4.1f}%   {rtxt}")
        secs.append(section(f"USAGE / LIMITS {C['dim']}(from /usage){C['x']}{stale}", ubody))
    elif snap is None:
        secs.append(section("USAGE / LIMITS", [
            f"    {C['dim']}run a Claude Code turn so the statusline writes the snapshot{C['x']}"]))

    daily = read_daily(snap, now)
    if daily:
        over = daily["frac"] > 1.0
        note = f"{C['r']}over daily limit{C['x']}" if over else f"{C['dim']}of daily limit{C['x']}"
        secs.append(section("DAILY BUDGET  (from 7d window)", [
            f"    used today {colorbar(daily['frac'], 12)} {daily['frac']*100:4.0f}%   {note}",
            f"    daily limit {daily['limit']:4.1f}%/day   "
            f"{C['dim']}{daily['used_today']:.1f}% of weekly used today{C['x']}",
            f"    sustainable {daily['sustainable']:4.1f}%/day   "
            f"{C['dim']}go-forward pace · {daily['days_left']:.1f}d left in window{C['x']}",
        ]))

    if rtk:
        secs.append(section(f"RTK PROXY SAVINGS {C['dim']}(global){C['x']}", [
            f"    commands {rtk['total_commands']:>7,}   "
            f"saved {C['g']}{fmt(rtk['total_saved'])}{C['x']} tok ({rtk['avg_savings_pct']:.1f}%)"]))
    return secs


def layout(secs, cols):
    if cols < 2:
        out = []
        for b in secs:
            out += b + [""]
        return out
    left, right, lh, rh = [], [], 0, 0
    for b in secs:
        if lh <= rh:
            left.append(b); lh += len(b) + 1
        else:
            right.append(b); rh += len(b) + 1
    L, R = [], []
    for b in left:
        L += b + [""]
    for b in right:
        R += b + [""]
    colw = max((vlen(x) for x in L), default=0) + 2
    out = []
    for i in range(max(len(L), len(R))):
        l = L[i] if i < len(L) else ""
        r = R[i] if i < len(R) else ""
        out.append(vpad(l, colw) + "  " + r)
    return out


def render(path, stats, rtk, snap, run_start, baseline_out, prev_out, now, refresh, cols=1):
    sid = os.path.basename(path)[:8]
    proj = os.path.basename(os.path.dirname(path))[:34]
    clock = datetime.now().strftime("%H:%M:%S")
    age = int(now - os.path.getmtime(path))
    secs = build_sections(path, stats, rtk, snap, run_start, baseline_out, prev_out, now)
    body = layout(secs, cols)
    width = max((vlen(x) for x in body), default=58)
    width = max(width, 60)
    head = f"{C['hdr']}╭─ tokenscope · Claude Code usage "
    head += "─" * max(2, width - vlen(head) - len(clock) - 3) + f" {clock} ─╮{C['x']}"
    out = [head,
           f"  session {C['b']}{sid}{C['x']} · {proj} · model {stats['model']} "
           f"{C['dim']}· updated {age}s ago{C['x']}",
           ""]
    out += body
    out.append(f"{C['dim']}  Ctrl-C to quit · refresh {refresh}s · {cols}-col (toggle with -c){C['x']}")
    return "\n".join(out)


def run(args):
    refresh = args.interval
    run_start = time.time()
    baseline_out = None
    prev_out = 0
    try:
        while True:
            now = time.time()
            snap = read_snapshot()
            path = args.file
            if not path and snap and snap.get("_age", 1e9) < 120:
                tp = snap.get("transcript_path")
                if tp and os.path.exists(tp):
                    path = tp
            if not path:
                path = pick_transcript(None, args.project)
            if not path or not os.path.exists(path):
                sys.stdout.write("\033[2J\033[H")
                print("No Claude Code transcript found under ~/.claude/projects yet.")
                print("Start or resume a session, then this will populate.")
                time.sleep(refresh)
                continue
            stats = read_session(path)
            if baseline_out is None:
                baseline_out = stats["totals"]["output"]
                prev_out = baseline_out
            rtk = read_rtk()
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(render(path, stats, rtk, snap, run_start, baseline_out,
                                    prev_out, now, refresh, args.columns) + "\n")
            sys.stdout.flush()
            prev_out = stats["totals"]["output"]
            time.sleep(refresh)
    except KeyboardInterrupt:
        sys.stdout.write("\033[0m\n")
        print("monitor stopped.")
