#!/usr/bin/env python3
"""tokcore — shared core for the tokenscope toolkit.

All token-stats tools (live monitor, multi-session grid, historical report, HTML
dashboard) and the statusline producer share these primitives so pricing, token
formatting, cost math, and the data-file locations live in exactly one place.

Data files (written by ~/.claude/statusline.sh every turn):
  usage-snapshot.json     latest harness payload — authoritative cost + rate limits
  sessions/{id}.json      per-session payload snapshot — powers the multi-session grid
  turn-log.jsonl          append-only per-turn history — powers report + dashboard
  tokenscope-daily.json   per-day baseline of the 7d limit — powers the daily budget
  rtk-cache.txt           "EPOCH SAVED_M PCT" cache of rtk proxy savings
"""
import glob
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone

# --- paths ---------------------------------------------------------------
CLAUDE = os.path.expanduser("~/.claude")
PROJECTS = os.path.join(CLAUDE, "projects")
SNAPSHOT = os.path.join(CLAUDE, "usage-snapshot.json")
# Claude Code's own per-process session registry ({pid}.json with sessionId, cwd,
# name, status, updatedAt). READ-ONLY for us — never write here.
REGISTRY_DIR = os.path.join(CLAUDE, "sessions")
# Our per-session payload snapshots, keyed by sessionId. Written by statusline.sh
# alongside the global usage-snapshot.json; joined to the registry for the grid.
SNAP_DIR = os.path.join(CLAUDE, "tokscope-sessions")
TURN_LOG = os.path.join(CLAUDE, "turn-log.jsonl")
DAILY_STATE = os.path.join(CLAUDE, "tokenscope-daily.json")
RTK_CACHE = os.path.join(CLAUDE, "rtk-cache.txt")

# --- pricing (USD per 1M tokens) — standard Opus rates; cost lines say "est." ---
PRICE = {
    "input": 15.0,        # fresh (uncached) input
    "output": 75.0,       # output
    "cache_read": 1.50,   # cache hit (0.1x input)
    "cache_5m": 18.75,    # 5-minute cache write (1.25x input)
    "cache_1h": 30.0,     # 1-hour cache write (2.0x input)
}
CONTEXT_WINDOW = 200_000  # fallback window size when the snapshot omits it

# --- ANSI palette (terminal) ---------------------------------------------
C = {"hdr": "\033[1;36m", "dim": "\033[2m", "g": "\033[32m",
     "y": "\033[33m", "r": "\033[31m", "b": "\033[1m", "x": "\033[0m"}
_ANSI = re.compile(r"\033\[[0-9;]*m")
SPARK = "▁▂▃▄▅▆▇█"


# --- formatting ----------------------------------------------------------
def fmt(n):
    """Human token count: 1234 -> 1,234 ; 3970600 -> 3.97M."""
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 10_000:
        return f"{n/1000:.1f}K"
    return f"{n:,}"


def toks(x):
    """Signed compact token count used by the report/dashboard."""
    a = abs(x)
    s = "-" if x < 0 else ""
    if a >= 1e6:
        return f"{s}{a/1e6:.2f}M"
    if a >= 1e3:
        return f"{s}{a/1e3:.1f}K"
    return f"{s}{a:.0f}"


def money(x):
    return f"${x:,.2f}"


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def hms(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


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


def bar(frac, width=20):
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def colorbar(frac, width=20):
    """Bar colored green/amber/red by fill level."""
    c = "\033[32m" if frac < 0.6 else ("\033[33m" if frac < 0.85 else "\033[31m")
    return c + bar(frac, width) + "\033[0m"


def sparkline(values, width=32):
    vals = [v for v in values if v is not None][-width:]
    if not vals:
        return ""
    hi = max(vals) or 1
    return "".join(SPARK[min(len(SPARK) - 1, int(v / hi * (len(SPARK) - 1)))] for v in vals)


def vlen(s):
    """Visible length, ignoring ANSI escapes."""
    return len(_ANSI.sub("", s))


def vpad(s, width):
    return s + " " * max(0, width - vlen(s))


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- snapshot / transcript readers ---------------------------------------
def read_snapshot(path=SNAPSHOT):
    try:
        with open(path) as fh:
            d = json.load(fh)
        d["_age"] = time.time() - os.path.getmtime(path)
        return d
    except Exception:
        return None


def cost_of(totals):
    return sum(totals[k] * PRICE[k] / 1_000_000 for k in totals)


def read_session(path):
    """Aggregate usage across a transcript .jsonl. Returns a stats dict."""
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
            if not (c5 or c1):
                c5 = u.get("cache_creation_input_tokens", 0) or 0
            totals["input"] += inp
            totals["output"] += out
            totals["cache_read"] += cr
            totals["cache_5m"] += c5
            totals["cache_1h"] += c1
            out_series.append(out)
            last_ctx = inp + cr + c5 + c1
    return {
        "totals": totals, "turns": turns, "model": model, "ctx": last_ctx,
        "cost": cost_of(totals), "first_ts": first_ts, "last_ts": last_ts,
        "out_series": out_series,
    }


def read_rtk():
    if not shutil.which("rtk"):
        return None
    try:
        out = subprocess.run(["rtk", "gain", "--format", "json"],
                             capture_output=True, text=True, timeout=5)
        return json.loads(out.stdout)["summary"]
    except Exception:
        return None


def read_daily(snap, now):
    """Synthesize a daily budget from the 7-day rolling limit.

    /usage exposes only 5h and 7d windows. We split the weekly headroom over the
    days left (sustainable %/day) and track how much of the 7d limit was spent
    since the start of the current UTC day via a persisted per-day baseline. All
    values are percentage-points of the weekly limit, so used-today and the
    fair-share daily limit compare 1:1.
    """
    rl = (snap or {}).get("rate_limits") if snap else None
    seg = (rl or {}).get("seven_day") or {}
    pct = seg.get("used_percentage")
    reset = seg.get("resets_at")
    if pct is None or not reset:
        return None
    days_left = max(0.25, (int(reset) - now) / 86400)
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    state = {}
    try:
        with open(DAILY_STATE) as fh:
            state = json.load(fh)
    except Exception:
        state = {}
    if state.get("day") != day or int(state.get("reset", 0)) != int(reset):
        state = {"day": day, "reset": int(reset), "base_7d": pct,
                 "budget": (100 - pct) / days_left}
        try:
            with open(DAILY_STATE, "w") as fh:
                json.dump(state, fh)
        except Exception:
            pass
    used_today = max(0.0, pct - state.get("base_7d", pct))
    fair = 100 / 7
    return {
        "limit": fair,
        "used_today": used_today,
        "frac": used_today / fair if fair else 0,
        "days_left": days_left,
        "sustainable": (100 - pct) / days_left,
    }


# --- multi-session discovery ---------------------------------------------
def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def discover_sessions(max_age=900):
    """Return one merged dict per open Claude Code session.

    Source of truth for *which* sessions exist is Claude Code's own registry
    (~/.claude/sessions/{pid}.json): we keep entries whose process is still alive
    (and, if max_age set, updated within that window). Cost/context/model come
    from our per-session snapshot (SNAP_DIR/{sessionId}.json) when present —
    joined by sessionId. Sessions with no snapshot yet still appear (registry-only),
    so a freshly-started session shows up before its first turn writes a snapshot.
    """
    out = []
    if not os.path.isdir(REGISTRY_DIR):
        return out
    now = time.time()
    for fp in glob.glob(os.path.join(REGISTRY_DIR, "*.json")):
        try:
            with open(fp) as fh:
                reg = json.load(fh)
        except Exception:
            continue
        if not _pid_alive(reg.get("pid")):
            continue
        age = max(0.0, now - (reg.get("updatedAt", 0) / 1000))
        if max_age and age > max_age:
            continue
        sid = reg.get("sessionId", "")
        snap = {}
        snap_fp = os.path.join(SNAP_DIR, f"{sid}.json")
        try:
            with open(snap_fp) as fh:
                snap = json.load(fh)
        except Exception:
            snap = {}
        merged = dict(snap)
        merged["session_id"] = sid
        merged["session_name"] = reg.get("name") or snap.get("session_name") or sid[:8]
        merged.setdefault("workspace", {})
        merged["workspace"]["current_dir"] = reg.get("cwd") or \
            (snap.get("workspace") or {}).get("current_dir", "")
        merged["_age"] = age
        merged["_status"] = reg.get("status", "")
        merged["_has_snapshot"] = bool(snap)
        out.append(merged)
    out.sort(key=lambda d: d.get("_age", 1e9))
    return out


# --- turn-log history (report + dashboard) -------------------------------
def load_turnlog(path=TURN_LOG, days=None, project=None):
    """Load turn-log rows; attaches _dt (local aware datetime). Filters by
    days (UTC cutoff) and project when given."""
    from datetime import timedelta
    rows = []
    if not os.path.exists(path):
        return rows
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt = parse_ts(r.get("ts"))
            if not dt:
                continue
            r["_dt"] = dt.astimezone()
            if cutoff and r["_dt"].astimezone(timezone.utc) < cutoff:
                continue
            if project and r.get("project") != project:
                continue
            rows.append(r)
    return rows


def peak_window(rows, hours=5):
    """Max total turn_cost within any rolling window of `hours` (sliding by turn).
    rows must each carry _dt. Returns (peak_cost, ending_datetime)."""
    from datetime import timedelta
    if not rows:
        return 0.0, None
    ev = sorted(rows, key=lambda r: r["_dt"])
    span = timedelta(hours=hours)
    best, best_end, lo, run = 0.0, None, 0, 0.0
    for hi in range(len(ev)):
        run += ev[hi].get("turn_cost", 0) or 0
        while ev[hi]["_dt"] - ev[lo]["_dt"] > span:
            run -= ev[lo].get("turn_cost", 0) or 0
            lo += 1
        if run > best:
            best, best_end = run, ev[hi]["_dt"]
    return best, best_end
