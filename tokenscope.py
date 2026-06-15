#!/usr/bin/env python3
"""
tokenscope — live usage & token monitor for Claude Code.

Reads the active session transcript under ~/.claude/projects/**/*.jsonl and shows
live token usage, context-window fill, a token-stats report, cost, output rate, and
the /usage rate limits (via the statusline snapshot). Overlays RTK proxy savings
(`rtk gain --format json`) when rtk is available.

Usage:
    python3 tokenscope.py                 # auto-pick the active session
    python3 tokenscope.py -i 2            # refresh every 2s (default 3)
    python3 tokenscope.py -c 2            # two-column layout
    python3 tokenscope.py -f <path.jsonl> # pin a specific transcript
    python3 tokenscope.py --project DIR   # restrict auto-pick to one project dir

Ctrl-C to quit.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# --- Pricing (USD per 1M tokens). EDIT if rates change — these are estimates. ---
# Defaults are standard Claude Opus rates. Cost line is labelled "est." for this reason.
PRICE = {
    "input": 15.0,        # fresh (uncached) input
    "output": 75.0,       # output
    "cache_read": 1.50,   # cache hit  (0.1x input)
    "cache_5m": 18.75,    # 5-minute cache write (1.25x input)
    "cache_1h": 30.0,     # 1-hour cache write   (2.0x input)
}
CONTEXT_WINDOW = 200_000  # Opus/Sonnet context size used for the fill bar

PROJECTS = os.path.expanduser("~/.claude/projects")
# Snapshot written by ~/.claude/statusline.sh — carries the /usage rate-limit
# data and Claude Code's authoritative session cost (the monitor has no stdin).
SNAPSHOT = os.path.expanduser("~/.claude/usage-snapshot.json")

SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values, width=32):
    vals = [v for v in values if v is not None][-width:]
    if not vals:
        return ""
    hi = max(vals) or 1
    return "".join(SPARK[min(len(SPARK) - 1, int(v / hi * (len(SPARK) - 1)))] for v in vals)


def fmt_countdown(epoch):
    if not epoch:
        return ""
    d = int(epoch) - int(time.time())
    if d <= 0:
        return "now"
    if d >= 86400:
        return f"{d//86400}d{(d%86400)//3600}h"
    if d >= 3600:
        return f"{d//3600}h{(d%3600)//60}m"
    return f"{d//60}m"


def read_snapshot():
    try:
        with open(SNAPSHOT) as fh:
            d = json.load(fh)
        d["_age"] = time.time() - os.path.getmtime(SNAPSHOT)
        return d
    except Exception:
        return None


def fmt(n):
    """Human-readable token count: 1234 -> 1,234 ; 3970600 -> 3.97M."""
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 10_000:
        return f"{n/1000:.1f}K"
    return f"{n:,}"


def bar(frac, width=20):
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def pick_transcript(pinned, project):
    if pinned:
        return pinned
    root = project or PROJECTS
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True) \
        if project else glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_session(path):
    """Aggregate usage across the transcript. Returns a dict of stats."""
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_5m": 0, "cache_1h": 0}
    turns = 0
    model = "?"
    last_ctx = 0
    first_ts = last_ts = None
    out_series = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(d.get("timestamp"))
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            msg = d.get("message") or {}
            u = msg.get("usage")
            if not u:
                continue
            turns += 1
            model = msg.get("model", model)
            inp = u.get("input_tokens", 0) or 0
            out = u.get("output_tokens", 0) or 0
            cr = u.get("cache_read_input_tokens", 0) or 0
            cc = u.get("cache_creation", {}) or {}
            c5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
            c1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
            if not (c5 or c1):  # fall back to flat field if breakdown absent
                c5 = u.get("cache_creation_input_tokens", 0) or 0
            totals["input"] += inp
            totals["output"] += out
            totals["cache_read"] += cr
            totals["cache_5m"] += c5
            totals["cache_1h"] += c1
            out_series.append(out)
            # context window for this turn ≈ everything fed in
            last_ctx = inp + cr + c5 + c1
    cost = sum(totals[k] * PRICE[k] / 1_000_000 for k in totals)
    return {
        "totals": totals, "turns": turns, "model": model, "ctx": last_ctx,
        "cost": cost, "first_ts": first_ts, "last_ts": last_ts,
        "out_series": out_series,
    }


def read_rtk():
    if not shutil.which("rtk"):
        return None
    try:
        out = subprocess.run(["rtk", "gain", "--format", "json"],
                             capture_output=True, text=True, timeout=5)
        s = json.loads(out.stdout)["summary"]
        return s
    except Exception:
        return None


def hms(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def colorbar(frac, width=20):
    """Bar colored green/amber/red by fill level."""
    c = "\033[32m" if frac < 0.6 else ("\033[33m" if frac < 0.85 else "\033[31m")
    return c + bar(frac, width) + "\033[0m"


C = {"hdr": "\033[1;36m", "dim": "\033[2m", "g": "\033[32m",
     "y": "\033[33m", "r": "\033[31m", "b": "\033[1m", "x": "\033[0m"}

_ANSI = re.compile(r"\033\[[0-9;]*m")


def vlen(s):
    """Visible length, ignoring ANSI escapes."""
    return len(_ANSI.sub("", s))


def vpad(s, width):
    return s + " " * max(0, width - vlen(s))


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def section(title, body):
    """A block: bold title line + indented body lines."""
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

    # CONTEXT
    secs.append(section("CONTEXT WINDOW", [
        f"    {colorbar(ctx_frac)} {ctx_frac*100:4.1f}%   {fmt(ctx_tok)} / {fmt(win)}",
    ]))

    # SESSION TOTALS
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

    # TOKEN STATS (derived report)
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

    # RATE
    arrow = f"{C['g']}+{tick_delta}{C['x']}" if tick_delta else f"{C['dim']}idle{C['x']}"
    rbody = [
        f"    output    {live_rate:6.0f} tok/min   last tick: {arrow}",
        f"    span      {hms(sess_elapsed)}   monitor {hms(elapsed_run)}",
    ]
    spark = sparkline(series, 40)
    if spark:
        rbody.append(f"    out/turn  {C['g']}{spark}{C['x']}")
    secs.append(section("RATE", rbody))

    # USAGE / LIMITS (from /usage snapshot)
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

    # RTK
    if rtk:
        secs.append(section(f"RTK PROXY SAVINGS {C['dim']}(global){C['x']}", [
            f"    commands {rtk['total_commands']:>7,}   "
            f"saved {C['g']}{fmt(rtk['total_saved'])}{C['x']} tok ({rtk['avg_savings_pct']:.1f}%)"]))

    return secs


def layout(secs, cols):
    """Stack sections (1 col) or place balanced side-by-side (2 col)."""
    if cols < 2:
        out = []
        for b in secs:
            out += b + [""]
        return out
    # balance by line count
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


def render(path, stats, rtk, snap, run_start, baseline_out, prev_out, now, cols=1):
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
    out.append(f"{C['dim']}  Ctrl-C to quit · refresh {REFRESH}s · {cols}-col (toggle with -c){C['x']}")
    return "\n".join(out)


REFRESH = 3


def main():
    global REFRESH
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--interval", type=float, default=3, help="refresh seconds")
    ap.add_argument("-f", "--file", help="pin a specific transcript .jsonl")
    ap.add_argument("--project", help="restrict auto-pick to this project dir")
    ap.add_argument("-c", "--columns", type=int, default=1, choices=[1, 2],
                    help="1 = stacked (default), 2 = side-by-side")
    args = ap.parse_args()
    REFRESH = args.interval

    run_start = time.time()
    baseline_out = None
    prev_out = 0

    try:
        while True:
            now = time.time()
            snap = read_snapshot()
            # Prefer the transcript the statusline says is active (fresh snapshot),
            # so we track the live session rather than any stray recent .jsonl.
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
                time.sleep(REFRESH)
                continue
            stats = read_session(path)
            if baseline_out is None:
                baseline_out = stats["totals"]["output"]
                prev_out = baseline_out  # avoid a bogus first-tick delta
            rtk = read_rtk()
            sys.stdout.write("\033[2J\033[H")  # clear + home
            sys.stdout.write(render(path, stats, rtk, snap, run_start, baseline_out, prev_out, now, args.columns) + "\n")
            sys.stdout.flush()
            prev_out = stats["totals"]["output"]
            time.sleep(REFRESH)
    except KeyboardInterrupt:
        sys.stdout.write("\033[0m\n")
        print("monitor stopped.")


if __name__ == "__main__":
    main()
