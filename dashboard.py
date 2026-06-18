#!/usr/bin/env python3
"""dashboard — self-contained interactive HTML from turn-log.jsonl (was tokstats_dashboard).

Embeds the turn history + Chart.js (CDN) into one HTML file; project/date filters
re-render client-side, no server. Also embeds a snapshot of the sessions open at
generation time (an "Active sessions" panel) — for a continuously-live multi-
session view use `tokenscope grid` in a terminal.
"""
import json
import os
import sys
import time
import webbrowser
from datetime import datetime, timezone

from tokcore import (TURN_LOG, discover_sessions, read_daily, read_rtk_cache,
                     read_snapshot)

OUT = os.path.expanduser("~/.claude/tokstats-dashboard.html")


def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "ts" not in r:
                continue
            try:
                dt = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            except ValueError:
                continue
            r["epoch"] = int(dt.timestamp() * 1000)
            rows.append(r)
    rows.sort(key=lambda r: r["epoch"])
    return rows


def session_cards():
    """Flatten open-session info into compact dicts for the HTML panel.

    Same source as `tokenscope grid`: all sessions with a live process (window=0).
    """
    out = []
    for s in discover_sessions(max_age=0):
        cw = s.get("context_window") or {}
        out.append({
            "name": s.get("session_name") or (s.get("session_id", "")[:8]),
            "project": os.path.basename((s.get("workspace") or {}).get("current_dir", "") or ""),
            "model": (s.get("model") or {}).get("display_name", "?"),
            "ctx": cw.get("used_percentage") or 0,
            "cost": (s.get("cost") or {}).get("total_cost_usd", 0) or 0,
            "age": int(s.get("_age", 0)),
            "status": s.get("_status", ""),
            "has_snapshot": bool(s.get("_has_snapshot")),
            "cache_hit": s.get("cache_hit"),
            "io_ratio": s.get("io_ratio"),
        })
    return out


def live_status():
    """Account-level live metrics that `tokenscope live` shows: current 5h/7d
    usage limits, the synthesized daily budget, and rtk savings. From the global
    snapshot, so it reflects the most recent turn across all sessions."""
    out = {"rate_limits": None, "daily": None, "rtk": None}
    snap = read_snapshot()
    if snap:
        rl = snap.get("rate_limits") or {}

        def seg(k):
            s = rl.get(k) or {}
            p = s.get("used_percentage")
            return None if p is None else {"pct": p, "resets_at": s.get("resets_at")}
        if seg("five_hour") or seg("seven_day"):
            out["rate_limits"] = {"five_hour": seg("five_hour"), "seven_day": seg("seven_day")}
        d = read_daily(snap, time.time())
        if d:
            out["daily"] = {"frac": d["frac"], "limit": d["limit"],
                            "used": d["used_today"], "sustainable": d["sustainable"],
                            "days_left": d["days_left"]}
    rtk = read_rtk_cache()
    if rtk:
        out["rtk"] = rtk
    return out


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tokenscope — Claude Code spend</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  /* "Quiet instrument panel" — muted neutrals, one accent, color reserved for signal.
     Inter via CDN with a system fallback so the static file still works offline. */
  :root{
    --bg:#0B0E14; --bg-soft:#0d111a; --card:#13171F; --card-2:#171c26;
    --line:#1F2530; --line-2:#2a313d;
    --txt:#E6EDF3; --dim:#9BA3AE; --faint:#5B6470;
    --accent:#3ECF8E;
    /* muted, lower-chroma data palette */
    --exact:#3ECF8E; --border:#5BB9D6; --partial:#D8A848; --red:#E0607E; --gray:#6b7280;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.18);
    --font:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  }
  /* Light — paper-white, darker accents for contrast on a light background. */
  :root[data-theme="light"]{
    --bg:#F7F8FA; --bg-soft:#FFFFFF; --card:#FFFFFF; --card-2:#EEF1F5;
    --line:#E3E7EC; --line-2:#D2D8DF;
    --txt:#1B2430; --dim:#566070; --faint:#8A94A2;
    --accent:#1F9D6B; --exact:#1F9D6B; --border:#2E86A8; --partial:#B07D1E; --red:#C8385C;
    --shadow:0 1px 2px rgba(20,30,50,.06), 0 8px 24px rgba(20,30,50,.07);
  }
  /* Yellowish — warm cream/sepia with an amber accent. */
  :root[data-theme="yellowish"]{
    --bg:#F4ECD8; --bg-soft:#FBF5E6; --card:#FBF6E9; --card-2:#F0E6CC;
    --line:#E4D7B5; --line-2:#D8C79C;
    --txt:#3A3320; --dim:#6B6038; --faint:#9A8C5C;
    --accent:#B8860B; --exact:#6E8B3D; --border:#3E7C8C; --partial:#B8860B; --red:#C0533B;
    --shadow:0 1px 2px rgba(120,90,20,.08), 0 8px 24px rgba(120,90,20,.09);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font:400 14px/1.5 var(--font);
    -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
  header{padding:22px 30px;border-bottom:1px solid var(--line);
    display:flex;align-items:center;gap:16px;flex-wrap:wrap;
    background:linear-gradient(180deg,var(--bg-soft),var(--bg))}
  h1{font-size:17px;margin:0;font-weight:600;letter-spacing:-.01em}
  h1 .z{color:var(--accent)}
  .controls{display:flex;gap:12px;align-items:center;margin-left:auto;flex-wrap:wrap}
  select,input{background:var(--card-2);color:var(--txt);border:1px solid var(--line-2);
    border-radius:8px;padding:7px 10px;font-size:13px;font-family:var(--font)}
  select:focus,input:focus{outline:none;border-color:var(--accent)}
  button{background:var(--card-2);color:var(--txt);border:1px solid var(--line-2);
    border-radius:8px;padding:7px 12px;font:500 12px var(--font);cursor:pointer}
  button:hover{border-color:var(--accent);color:var(--accent)}
  button.on{border-color:var(--accent);color:var(--accent)}
  /* notifications popover */
  .pop{position:relative;display:inline-block}
  .pop-panel{position:absolute;right:0;top:calc(100% + 8px);z-index:40;display:none;
    width:300px;padding:14px;background:var(--card);border:1px solid var(--line-2);
    border-radius:12px;box-shadow:var(--shadow)}
  .pop-panel.open{display:block}
  .pop-panel h3{margin:0 0 4px;font:600 12px var(--font);text-transform:uppercase;
    letter-spacing:.06em;color:var(--dim)}
  .pop-panel .hint{color:var(--faint);font-size:11px;margin:0 0 12px;line-height:1.5}
  .nrow{display:flex;align-items:center;gap:8px;margin:9px 0}
  .nrow .nlabel{flex:1;display:flex;align-items:center;gap:5px;font-size:13px;color:var(--txt)}
  .nrow select{padding:4px 7px;font-size:12px}
  .nrow input[type=checkbox]{width:15px;height:15px;accent-color:var(--accent);cursor:pointer}
  /* small ⓘ with hover tooltip, reused from h2[data-desc] styling */
  .info{cursor:help;color:var(--faint);font-size:10px;border:1px solid var(--line-2);
    border-radius:50%;width:14px;height:14px;display:inline-flex;align-items:center;
    justify-content:center;position:relative}
  .info:hover::before{content:attr(data-desc);position:absolute;left:50%;transform:translateX(-50%);
    bottom:calc(100% + 7px);z-index:50;width:230px;padding:9px 11px;background:var(--card-2);
    border:1px solid var(--line-2);border-radius:9px;box-shadow:var(--shadow);color:var(--txt);
    font:400 11.5px/1.5 var(--font);text-transform:none;letter-spacing:normal}
  .pop-note{color:var(--faint);font-size:11px;line-height:1.5}
  /* volume row */
  .nrow input[type=range]{flex:1;accent-color:var(--accent);cursor:pointer}
  .nrow .vval{width:38px;text-align:right;font-size:12px;color:var(--dim);font-variant-numeric:tabular-nums}
  /* per-entry: clickable title/card or ⤢ button → select (highlight) + overlay */
  h2.exp{cursor:pointer;user-select:none}
  .kpi.exp{cursor:pointer}
  .kpi.exp:hover{border-color:var(--line-2)}
  /* top-right expand button on every chart card */
  .expand-btn{position:absolute;top:12px;right:12px;z-index:5;background:var(--card-2);
    color:var(--faint);border:1px solid var(--line-2);border-radius:8px;width:26px;height:26px;
    font-size:13px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center;
    opacity:0;transition:opacity .12s,color .12s,border-color .12s}
  .card:hover .expand-btn{opacity:1}
  .expand-btn:hover{color:var(--accent);border-color:var(--accent)}
  /* selected highlight — contained so only THIS card reads as selected
     (a tight accent ring + subtle tint, no wide halo that bleeds onto neighbors) */
  .kpi.sel,.card.sel{border-color:var(--accent)!important;
    box-shadow:inset 0 0 0 1px var(--accent), 0 0 0 3px color-mix(in srgb,var(--accent) 26%,transparent), var(--shadow);
    background:color-mix(in srgb,var(--accent) 7%,var(--card));
    transition:box-shadow .15s,border-color .15s,background .15s}
  .card.sel .expand-btn{opacity:1;color:var(--accent);border-color:var(--accent)}
  /* detail overlay */
  .ovl{position:fixed;inset:0;z-index:100;display:none;align-items:center;justify-content:center;
    background:color-mix(in srgb,var(--bg) 62%,transparent);backdrop-filter:blur(3px);padding:24px}
  .ovl.open{display:flex;animation:fade .14s ease-out}
  .ovl-card{background:var(--card);border:1px solid var(--line-2);border-radius:16px;
    box-shadow:0 20px 60px rgba(0,0,0,.4);max-width:820px;width:100%;max-height:88vh;overflow:auto}
  .ovl-chart{height:46vh;max-height:420px;margin:14px 22px 0;position:relative;display:none}
  .ovl-chart.show{display:block}
  .ovl-chart canvas{max-height:none!important}
  .ovl-head{display:flex;align-items:flex-start;gap:12px;padding:20px 22px 0}
  .ovl-head h3{margin:0;font-size:18px;font-weight:600;letter-spacing:-.01em;flex:1}
  .ovl-head .tag{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em;margin-top:5px}
  .ovl-close{background:none;border:none;color:var(--faint);font-size:20px;cursor:pointer;
    padding:0 4px;line-height:1}
  .ovl-close:hover{color:var(--accent)}
  .ovl-val{padding:6px 22px 0;font-size:30px;font-weight:600;letter-spacing:-.02em;
    font-variant-numeric:tabular-nums;color:var(--accent)}
  .ovl-body{padding:14px 22px 22px;font-size:13.5px;line-height:1.65;color:var(--dim)}
  .ovl-body h4{margin:16px 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--faint)}
  .ovl-body h4:first-child{margin-top:4px}
  .ovl-body b{color:var(--txt);font-weight:600}
  /* a section can host its own controls on the right of the rule */
  .section::after{order:1}
  .section .sctl{order:2;margin-left:10px;text-transform:none;letter-spacing:0;font-weight:400}
  /* activity heatmap */
  .heat{display:grid;grid-template-columns:34px repeat(24,1fr);gap:3px;align-items:center}
  .heat .hh{font-size:10px;color:var(--faint);text-align:center}
  .heat .dl{font-size:10px;color:var(--faint);text-align:right;padding-right:6px}
  .heat .cell{aspect-ratio:1;border-radius:3px;background:var(--line);min-height:13px}
  .heat-legend{display:flex;align-items:center;gap:6px;justify-content:flex-end;
    margin-top:10px;font-size:11px;color:var(--faint)}
  .heat-legend i{width:13px;height:13px;border-radius:3px;display:inline-block}
  label{color:var(--faint);font-size:11px;letter-spacing:.04em;text-transform:uppercase;margin-right:5px}
  .wrap{padding:26px 30px;max-width:1320px;margin:0 auto}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:14px;margin-bottom:24px}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;
    box-shadow:var(--shadow)}
  .kpi .v{font-size:28px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
  .kpi .l{color:var(--faint);font-size:11px;margin-top:5px;text-transform:uppercase;letter-spacing:.05em}
  .kpi.exact .v{color:var(--exact)} .kpi.partial .v{color:var(--partial)}
  .kpi.border .v{color:var(--border)} .kpi.red .v{color:var(--red)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;
    box-shadow:var(--shadow);position:relative}
  .zreset{position:absolute;top:12px;right:14px;display:none;z-index:5;cursor:pointer;
    font-size:11px;padding:3px 9px;border-radius:7px;background:var(--card-2);
    border:1px solid var(--line-2);color:var(--faint)}
  .zreset:hover{border-color:var(--accent);color:var(--accent)}
  .card h2{font-size:12px;margin:0 0 14px;color:var(--dim);font-weight:500;
    text-transform:uppercase;letter-spacing:.07em}
  /* Section description: hover the header only (not the data inside). */
  h2[data-desc]{cursor:help;position:relative;display:inline-block}
  h2[data-desc]::after{content:"ⓘ";margin-left:6px;color:var(--faint);font-size:9px;
    vertical-align:super;opacity:.6}
  h2[data-desc]:hover::before{
    content:attr(data-desc);
    position:absolute;left:0;top:calc(100% + 6px);z-index:30;
    width:320px;max-width:72vw;padding:10px 13px;
    background:var(--card-2);border:1px solid var(--line-2);border-radius:10px;
    box-shadow:var(--shadow);color:var(--txt);
    font:400 12px/1.55 var(--font);
    text-transform:none;letter-spacing:normal;white-space:normal;
    animation:fade .12s ease-out}
  @keyframes fade{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}
  .card.full{grid-column:1/-1}
  canvas{max-height:300px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:8px 8px;border-bottom:1px solid var(--line)}
  tr:last-child td{border-bottom:none}
  th{color:var(--faint);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
  tbody tr{transition:background .12s} tbody tr:hover{background:var(--card-2)}
  td.n{text-align:right;font-variant-numeric:tabular-nums}
  .ctxbar{display:block;height:6px;border-radius:3px;background:var(--accent)}
  .ctxpct{display:inline-block;width:34px;text-align:right;font-variant-numeric:tabular-nums}
  .ctxtrack{display:inline-block;width:90px;height:6px;border-radius:3px;background:var(--line-2);vertical-align:middle;overflow:hidden}
  .pill{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--accent);margin-right:6px;vertical-align:middle;
    box-shadow:0 0 6px color-mix(in srgb,var(--accent) 70%,transparent)}
  .pill.recent{background:var(--partial);box-shadow:0 0 6px color-mix(in srgb,var(--partial) 70%,transparent)}
  .pill.idle{background:var(--faint);box-shadow:none}
  .muted{color:var(--faint)}
  .sess-active{color:var(--dim);font-size:12px;margin:0 0 12px}
  .sess-active .a{color:var(--accent);font-weight:600} .sess-active .r{color:var(--partial);font-weight:600}
  .proj-chip{display:inline-block;background:var(--card-2);border:1px solid var(--line-2);border-radius:20px;
    padding:2px 9px;margin:0 5px 5px 0;font-size:12px}
  .proj-chip .d{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;vertical-align:middle;background:var(--accent)}
  .proj-chip.rec .d{background:var(--partial)}
  .livegrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px 26px}
  .lv{display:flex;align-items:center;gap:9px;font-size:13px}
  .lv .lvl{display:inline-block;width:42px;color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
  .lv b{font-weight:600;font-variant-numeric:tabular-nums}
  #liveBadge{display:none;align-items:center;gap:7px;font-size:12px;font-weight:500;color:var(--accent);
    border:1px solid var(--line-2);border-radius:20px;padding:4px 11px}
  #liveBadge::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent);
    box-shadow:0 0 8px var(--accent);animation:pulse 1.8s infinite}
  #liveBadge.stale{color:var(--partial)} #liveBadge.stale::before{background:var(--partial);box-shadow:none;animation:none}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  .section{display:flex;align-items:center;gap:12px;margin:30px 0 14px;
    font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.09em;color:var(--dim)}
  .section:first-child{margin-top:6px}
  .section::after{content:"";flex:1;height:1px;background:var(--line)}
  .section .n{font-weight:400;color:var(--faint);text-transform:none;letter-spacing:0}
  .sess-note{color:var(--faint);font-size:11px;margin-top:10px}
  .legend{font-size:11px;color:var(--faint);margin-top:16px;line-height:1.8}
  .dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
  @media(max-width:820px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1><span class="z">token</span>scope · Claude Code spend</h1>
  <span id="liveBadge">live</span>
  <div class="controls">
    <span><label>Project</label><select id="fProj"></select></span>
    <span><label>Model</label><select id="fModel"></select></span>
    <span><label>From</label><input type="date" id="fFrom"></span>
    <span><label>To</label><input type="date" id="fTo"></span>
    <button id="exportBtn" title="Download the filtered turns as CSV">Export CSV</button>
    <span id="pollWrap" style="display:none"><label title="How often the dashboard re-polls live data">Refresh</label><select id="pollSel"></select></span>
    <span><label>Theme</label><select id="themeSel">
      <option value="dark">Dark</option><option value="light">Light</option>
      <option value="yellowish">Yellowish</option></select></span>
  </div>
</header>
<div class="wrap">
  <div class="section">Live <span class="sctl" style="display:inline-flex;align-items:center;gap:5px"><label style="margin:0">recent ≤</label><input type="number" id="recentMin" min="1" max="60" style="width:48px" title="A just-idle session stays amber ('recent') for this many minutes after its last activity"> min</span> <span class="sctl pop">
      <button id="alertsBtn" title="Notification sounds — rings that play when a session needs you. Click to configure.">🔔 Alerts</button>
      <div class="pop-panel" id="alertsPanel">
        <h3>Notification sounds</h3>
        <p class="hint">Sounds your machine plays when a session here wants your attention. Configured here, played by the <code>Stop</code>/<code>Notification</code> hooks.</p>
        <div id="alertsBody">
          <div class="nrow">
            <input type="checkbox" id="mMaster">
            <span class="nlabel">All sounds
              <span class="info" data-desc="Master switch. Off = total silence regardless of the per-event toggles below.">i</span>
            </span>
          </div>
          <div class="nrow">
            <span class="nlabel" style="flex:0 0 auto">Volume
              <span class="info" data-desc="Playback level for every notification sound (passed to afplay -v). 0 = inaudible.">i</span>
            </span>
            <input type="range" id="vVol" min="0" max="100" step="5">
            <span class="vval" id="vVolN">80%</span>
          </div>
          <div class="nrow">
            <input type="checkbox" id="eIdle">
            <span class="nlabel">Session idle
              <span class="info" data-desc="Plays when a session finishes responding and hands control back to you — your cue to return. (Stop hook.)">i</span>
            </span>
            <select id="sIdle"></select>
          </div>
          <div class="nrow">
            <input type="checkbox" id="eNeed">
            <span class="nlabel">Needs your input
              <span class="info" data-desc="Plays when Claude is waiting on you mid-task — a permission prompt or a requested answer. (Notification hook.)">i</span>
            </span>
            <select id="sNeed"></select>
          </div>
        </div>
        <p class="pop-note" id="alertsNote" style="display:none"></p>
      </div>
    </span></div>
  <div class="card full" id="liveCard" style="margin-bottom:16px;display:none">
    <h2 data-desc="Current 5h/7d subscription rate-limit usage, the daily budget synthesized from the 7-day window, and rtk proxy savings. Account-wide, from the latest turn.">Usage limits &amp; budget <span id="liveMode" style="text-transform:none;font-weight:400"></span></h2>
    <div id="liveBody" class="livegrid"></div>
  </div>
  <div class="card full" id="sessCard" style="display:none">
    <h2 data-desc="Every Claude Code session with a live process — joined from the session registry (names, liveness) and per-session snapshots (cost, context). Cache hit & in:out are aggregated from each transcript.">Active sessions <span id="sessMode" style="text-transform:none;font-weight:400"></span></h2>
    <div class="sess-active" id="sessActive"></div>
    <table id="tSess"><thead><tr><th></th><th>Session</th><th>Project</th><th>Model</th><th class="n">Context</th><th class="n">Cache hit</th><th class="n">In:out</th><th class="n">Cost</th><th class="n">Active</th></tr></thead><tbody></tbody></table>
    <div class="sess-note" id="sessNote"></div>
  </div>

  <div class="section">Overview <span class="n">· selected range · click a card for detail</span></div>
  <div class="kpis" id="kpis"></div>

  <div class="section">Spend <span class="n">· click a chart title for detail</span> <span class="sctl pop">
      <button id="chartBtn" title="Chart inspection options — markers, exact lines, gridlines, drag-to-zoom.">⚙ Chart</button>
      <div class="pop-panel" id="chartPanel">
        <h3>Chart options</h3>
        <p class="hint">For precise reading rather than at-a-glance shape — applies to every chart below. Tip: <b>drag</b> across a chart to zoom, <b>double-click</b> to reset.</p>
        <div class="nrow"><input type="checkbox" id="oPoints">
          <span class="nlabel">Data points
            <span class="info" data-desc="Draw a marker at every data point so you can read individual turns/days exactly, not just the trend line.">i</span></span></div>
        <div class="nrow"><input type="checkbox" id="oExact">
          <span class="nlabel">Exact lines
            <span class="info" data-desc="Turn off curve smoothing so lines connect points directly — no interpolation that can misread between samples.">i</span></span></div>
        <div class="nrow"><input type="checkbox" id="oGrid">
          <span class="nlabel">Vertical gridlines
            <span class="info" data-desc="Add x-axis gridlines to line up points with their dates/values more accurately.">i</span></span></div>
      </div>
    </span></div>
  <div class="grid">
    <div class="card full" data-entry="day"><h2 class="exp">Spend per day</h2><canvas id="cDay"></canvas></div>
    <div class="card" data-entry="cum"><h2 class="exp">Cumulative spend</h2><canvas id="cCum"></canvas></div>
    <div class="card" data-entry="scatter"><h2 class="exp">Cost vs. tokens per turn</h2><canvas id="cScatter"></canvas>
      <div class="heat-legend">older <i style="width:64px;background:linear-gradient(90deg,#6E8BFF,#3ECF8E)"></i> recent</div></div>
    <div class="card" data-entry="proj"><h2 class="exp">Spend by project</h2><canvas id="cProj"></canvas></div>
    <div class="card" data-entry="model"><h2 class="exp">Spend by model</h2><canvas id="cModel"></canvas></div>
  </div>

  <div class="section">Tokens &amp; cache</div>
  <div class="grid">
    <div class="card"><h2 data-desc="Cache read vs cache write tokens per day — usually the bulk of traffic, and far cheaper than fresh input.">Cache tokens per day (read vs. write)</h2><canvas id="cCache"></canvas></div>
    <div class="card"><h2 data-desc="Daily cache-hit ratio: cache_read / (read + write). Higher = more of your context is served cheaply from cache.">Cache-hit % per day</h2><canvas id="cHit"></canvas></div>
  </div>

  <div class="section">Context window</div>
  <div class="grid">
    <div class="card full"><h2 data-desc="Context-window fill % at each turn. Red markers are compactions (turn_tokens<0) — where context was trimmed/cleared.">Context fill &amp; compactions</h2><canvas id="cCtx"></canvas></div>
  </div>

  <div class="section">Activity</div>
  <div class="grid">
    <div class="card full"><h2 data-desc="Spend by hour-of-day and weekday (local time) — darker = more cost. Reveals when your usage concentrates.">Activity heatmap (spend by hour × weekday)</h2>
      <div id="heat" class="heat"></div>
      <div class="heat-legend">less <i style="background:var(--line)"></i><i style="background:rgba(62,207,142,.35)"></i><i style="background:rgba(62,207,142,.65)"></i><i style="background:var(--accent)"></i> more</div>
    </div>
  </div>

  <div class="section">Usage limits over time</div>
  <div class="grid">
    <div class="card full"><h2 data-desc="Total cost in the trailing 5 hours at each turn — a proxy for the 5h subscription usage limit.">5-hour rolling window (usage-limit proxy)</h2><canvas id="cRoll"></canvas></div>
    <div class="card full"><h2 data-desc="The 5h and 7d rate-limit usage % as recorded at each turn (only turns that carried the fields).">Rate-limit burn over time (5h / 7d %)</h2><canvas id="cLimits"></canvas></div>
  </div>

  <div class="section">Top turns</div>
  <div class="grid">
    <div class="card full"><h2 data-desc="The most expensive individual turns in the selected range.">Top 12 turns by cost</h2>
      <table id="tTop"><thead><tr><th>When</th><th>Project</th><th>Model</th><th class="n">Cost</th><th class="n">Tokens</th><th class="n">Cache</th><th class="n">Ctx</th></tr></thead><tbody></tbody></table>
    </div>
  </div>

  <div class="legend">
    <span class="dot" style="background:var(--exact)"></span><b>Cost</b> — exact, includes subagent spend &nbsp;&nbsp;
    <span class="dot" style="background:var(--partial)"></span><b>Tokens</b> — main-loop only, excludes subagents (can go negative on compaction) &nbsp;&nbsp;
    <span class="dot" style="background:var(--border)"></span><b>5h window</b> — exact values, heuristic turn-slicing
    <br>Generated __GEN__ · __N__ turns · data: ~/.claude/turn-log.jsonl
  </div>
</div>
<div class="ovl" id="entryOvl">
  <div class="ovl-card">
    <div class="ovl-head">
      <div style="flex:1"><h3 id="ovlTitle"></h3><div class="tag" id="ovlTag"></div></div>
      <button class="ovl-close" id="ovlClose" title="Close (Esc)">✕</button>
    </div>
    <div class="ovl-val" id="ovlVal"></div>
    <div class="ovl-chart" id="ovlChart"></div>
    <div class="ovl-body" id="ovlBody"></div>
  </div>
</div>
<script>
let DATA = __DATA__;
let SESSIONS = __SESSIONS__;
let LIVEINFO = __LIVE_INFO__;
const LIVE = __LIVE__;
const POLL_MS = __POLL__;
const $ = s => document.querySelector(s);
const money = x => "$" + x.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const toks = x => { const a=Math.abs(x), s=x<0?"-":"";
  return a>=1e6 ? s+(a/1e6).toFixed(2)+"M" : a>=1e3 ? s+(a/1e3).toFixed(1)+"K" : s+a; };
const COL = {exact:"#3ECF8E", partial:"#D8A848", border:"#5BB9D6", red:"#E0607E"};
// muted, lower-chroma data palette — color stays calm so signal stands out
const PALETTE = ["#3ECF8E","#6E8BFF","#5BB9D6","#D8A848","#E0607E","#9b87f5","#7C8694","#4cd4b0"];
// claude-opus-4-8 -> Opus 4-8 ; claude-3-5-haiku-20241022 -> 3-5-haiku ...
const modelShort = m => !m ? "?" : m.replace(/^claude-/,"").replace(/-\d{8}$/,"")
  .replace(/^(opus|sonnet|haiku)/i, s=>s[0].toUpperCase()+s.slice(1));
Chart.defaults.color = "#5B6470";
Chart.defaults.borderColor = "#1F2530";
Chart.defaults.font.family = '"Inter",-apple-system,"Segoe UI",Roboto,sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.elements.line.borderWidth = 1.5;
Chart.defaults.elements.line.tension = 0.32;
Chart.defaults.elements.point.radius = 0;
Chart.defaults.elements.point.hoverRadius = 4;
Chart.defaults.elements.bar.borderRadius = 4;
Chart.defaults.plugins.tooltip.backgroundColor = "#171c26";
Chart.defaults.plugins.tooltip.borderColor = "#2a313d";
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.titleColor = "#E6EDF3";
Chart.defaults.plugins.tooltip.bodyColor = "#9BA3AE";
Chart.defaults.plugins.tooltip.padding = 10;
Chart.defaults.plugins.tooltip.cornerRadius = 8;
Chart.defaults.plugins.tooltip.displayColors = false;
// Hover ANYWHERE over a chart and read the value at the nearest x — and every
// series at that x at once — instead of Chart.js's default "hover exactly on a
// (radius-0, invisible) point". This is the single biggest navigation win for the
// time-series line charts. Doughnut/scatter charts opt back out per-chart below.
Chart.defaults.interaction.mode = "index";
Chart.defaults.interaction.intersect = false;
Chart.defaults.interaction.axis = "x";
Chart.defaults.plugins.tooltip.mode = "index";
Chart.defaults.plugins.tooltip.intersect = false;

// Crosshair: a faint dashed vertical guide at the hovered x, so a point lines up
// with its axis label. Cartesian charts only (a vertical line on a doughnut is junk).
Chart.register({
  id:"crosshair",
  afterDraw(chart){
    if(chart.config.type==="doughnut"||chart.config.type==="pie") return;
    const t=chart.tooltip;
    if(!t||!t._active||!t._active.length) return;
    const x=t._active[0].element.x, {top,bottom}=chart.chartArea, c=chart.ctx;
    c.save(); c.beginPath(); c.moveTo(x,top); c.lineTo(x,bottom);
    c.lineWidth=1; c.strokeStyle="rgba(91,185,214,.35)"; c.setLineDash([4,3]);
    c.stroke(); c.restore();
  }
});

// Reset-zoom affordance: a small button that appears on a chart ONLY while it's
// zoomed/panned (progressive disclosure), so the reset isn't a hidden double-click.
Chart.register({
  id:"resetBtn",
  afterDraw(chart){
    if(!chart.isZoomedOrPanned) return;   // zoom plugin not loaded
    const card=chart.canvas.closest(".card"); if(!card) return;
    let b=card.querySelector(".zreset");
    if(!b){
      b=document.createElement("button"); b.className="zreset"; b.textContent="⟲ reset zoom";
      b.addEventListener("click",()=>chart.resetZoom());
      card.appendChild(b);
    }
    b.style.display = chart.isZoomedOrPanned() ? "block" : "none";
  }
});
// Drag-to-zoom for fine inspection (chartjs-plugin-zoom, if it loaded).
// Drag a range on the x-axis to zoom; wheel to zoom; double-click any chart to reset.
// IMPORTANT: merge into the plugin's own defaults — overwriting the whole object
// drops its `pan`/`limits` keys, which the plugin then reads → a TypeError that
// aborts chart rendering. Mutate sub-keys only.
if (window.Chart && Chart.defaults.plugins && Chart.defaults.plugins.zoom
    && Chart.defaults.plugins.zoom.zoom){
  const z = Chart.defaults.plugins.zoom.zoom;
  z.wheel.enabled = true;
  z.drag.enabled = true;
  z.drag.backgroundColor = "rgba(91,185,214,.18)";
  z.drag.borderColor = "#5BB9D6";
  z.drag.borderWidth = 1;
  z.mode = "x";
}
document.addEventListener("dblclick", e=>{
  if(e.target && e.target.tagName==="CANVAS"){
    const c=Chart.getChart(e.target); if(c && c.resetZoom) c.resetZoom();
  }
});

// Soft vertical gradient fill: opaque-ish at the line, fading to transparent.
function grad(hex){
  return (ctx)=>{
    const {chart}=ctx, area=chart.chartArea;
    if(!area) return hex+"22";
    const g=chart.ctx.createLinearGradient(0,area.top,0,area.bottom);
    g.addColorStop(0,hex+"3A"); g.addColorStop(1,hex+"00");
    return g;
  };
}
// y-axis only, faint dashed gridlines; no x gridlines (less chart junk)
const GRID = {x:{grid:{display:false},border:{display:false}},
              y:{grid:{color:"#1A1F29",drawTicks:false},border:{display:false}}};

const fmtDay = e => { const d=new Date(e); return d.getFullYear()+"-"+
  String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); };
const fmtAge = s => s<90 ? s+"s" : s<5400 ? Math.round(s/60)+"m" : Math.round(s/3600)+"h";

const selP = $("#fProj");
const selM = $("#fModel");

const lvlColor = p => p<60?COL.exact : p<85?COL.partial : COL.red;
const pctBar = (p,c) => `<span class="ctxtrack"><span class="ctxbar" style="width:${Math.min(100,Math.max(0,p))}%;background:${c}"></span></span>`;
function fmtReset(epoch){
  if(!epoch) return "";
  let d = epoch - Math.floor(Date.now()/1000);
  if(d<=0) return "now";
  if(d>=86400) return Math.floor(d/86400)+"d"+Math.floor(d%86400/3600)+"h";
  if(d>=3600)  return Math.floor(d/3600)+"h"+Math.floor(d%3600/60)+"m";
  return Math.floor(d/60)+"m";
}

// Account-level live status: current 5h/7d limits, daily budget, rtk savings —
// the same figures `tokenscope live` shows.
function renderLive(){
  const card = $("#liveCard");
  const info = LIVEINFO || {};
  const items = [];
  const rl = info.rate_limits || {};
  [["five_hour","5h"],["seven_day","7d"]].forEach(([k,lbl])=>{
    const s = rl[k]; if(!s) return;
    items.push(`<div class="lv"><span class="lvl">${lbl}</span>${pctBar(s.pct,lvlColor(s.pct))}`+
      `<b>${s.pct.toFixed(0)}%</b><span class="muted">↻${fmtReset(s.resets_at)}</span></div>`);
  });
  const d = info.daily;
  if(d){
    const p = d.frac*100;
    items.push(`<div class="lv"><span class="lvl">today</span>${pctBar(p,lvlColor(p))}`+
      `<b>${p.toFixed(0)}%</b><span class="muted">of ${d.limit.toFixed(1)}%/day · `+
      `${d.sustainable.toFixed(1)}%/day sustainable · ${d.days_left.toFixed(1)}d left</span></div>`);
  }
  const rtk = info.rtk;
  if(rtk){
    items.push(`<div class="lv"><span class="lvl">rtk</span>`+
      `<b>↓${Number(rtk.pct).toFixed(0)}%</b><span class="muted">saved ${rtk.saved} tok</span></div>`);
  }
  card.style.display = items.length ? "" : "none";
  $("#liveBody").innerHTML = items.join("");
}

// "recently active" threshold (seconds): idle but seen within this window → amber.
let RECENT_SECS=120; try{const r=localStorage.getItem("ts-recent"); if(r)RECENT_SECS=Math.max(60,+r*60);}catch(e){}
const sessState=s=>(s.status==="busy"||s.status==="waiting") ? "active"
                  : ((s.age||0) < RECENT_SECS ? "recent" : "idle");
const STRANK={active:0,recent:1,idle:2};
function renderSessions(){
  const card = $("#sessCard");
  if (!SESSIONS.length){ card.style.display = "none"; return; }
  card.style.display = "";
  // active first, then recent, then idle; within a group, freshest first
  const rows=[...SESSIONS].sort((a,b)=>STRANK[sessState(a)]-STRANK[sessState(b)] || (a.age||0)-(b.age||0));
  $("#tSess tbody").innerHTML = rows.map(s=>{
    const st=sessState(s), pillc = st==="active" ? "" : st;
    const ctxc = s.ctx<60?COL.exact:s.ctx<85?COL.partial:COL.red;
    const ctxCell = s.has_snapshot
      ? `<span class="ctxtrack"><span class="ctxbar" style="width:${Math.min(100,s.ctx)}%;background:${ctxc}"></span></span> <span class="ctxpct">${s.ctx}%</span>`
      : `<span class="muted">no turn yet</span>`;
    const costCell = s.has_snapshot ? money(s.cost) : "—";
    const hitCell = s.cache_hit!=null ? (s.cache_hit*100).toFixed(0)+"%" : '<span class="muted">—</span>';
    const ioCell  = s.io_ratio ? s.io_ratio.toFixed(0)+":1" : '<span class="muted">—</span>';
    return `<tr><td><span class="pill ${pillc}" title="${st}"></span></td>`+
      `<td>${s.name}</td><td>${s.project}</td><td>${s.has_snapshot?s.model:"?"}</td>`+
      `<td class="n">${ctxCell}</td>`+
      `<td class="n">${hitCell}</td><td class="n">${ioCell}</td>`+
      `<td class="n">${costCell}</td><td class="n">${fmtAge(s.age)} ago</td></tr>`;
  }).join("");
  // parallel-projects summary: counts + one chip per project with live (active/recent) work
  const nA=SESSIONS.filter(s=>sessState(s)==="active").length;
  const nR=SESSIONS.filter(s=>sessState(s)==="recent").length;
  const nI=SESSIONS.length-nA-nR;
  const grp={};
  SESSIONS.forEach(s=>{const st=sessState(s); if(st==="idle")return; const p=s.project||"?";
    if(grp[p]===undefined||STRANK[st]<STRANK[grp[p]]) grp[p]=st;});
  const chips=Object.entries(grp).sort((a,b)=>STRANK[a[1]]-STRANK[b[1]])
    .map(([p,st])=>`<span class="proj-chip ${st==="recent"?"rec":""}"><span class="d"></span>${p}</span>`).join("");
  $("#sessActive").innerHTML =
    `<span class="a">${nA} active</span> · <span class="r">${nR} recent</span> · ${nI} idle`
    + (chips ? ` &nbsp;·&nbsp; working in parallel: ${chips}` : "");
}

// Repopulate the project/model filters, preserving the current selection.
function populateProjects(){
  const cur = selP.value;
  const projects = [...new Set(DATA.map(r=>r.project))].sort();
  selP.innerHTML = '<option value="">All projects</option>' +
    projects.map(p=>`<option>${p}</option>`).join("");
  if (cur && projects.includes(cur)) selP.value = cur;

  const curM = selM.value;
  const models = [...new Set(DATA.map(r=>r.model).filter(Boolean))].sort();
  selM.innerHTML = '<option value="">All models</option>' +
    models.map(m=>`<option value="${m}">${modelShort(m)}</option>`).join("");
  if (curM && models.includes(curM)) selM.value = curM;
}

// Default the date range once (don't clobber a user's choice on live refresh).
function initDates(){
  if (DATA.length && !$("#fFrom").value) $("#fFrom").value = fmtDay(DATA[0].epoch);
  if (DATA.length && !$("#fTo").value)   $("#fTo").value   = fmtDay(DATA[DATA.length-1].epoch);
}

// Activity heatmap: 7 weekdays × 24 hours, cell opacity scaled to cost (local time).
const DOW = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
function renderHeatmap(rows){
  const grid = Array.from({length:7},()=>new Array(24).fill(0));
  rows.forEach(r=>{ const d=new Date(r.epoch); grid[d.getDay()][d.getHours()] += r.turn_cost||0; });
  let max=0; grid.forEach(row=>row.forEach(v=>{ if(v>max) max=v; }));
  const bg = v => (!max||!v) ? "var(--line)" : `rgba(62,207,142,${(0.16+v/max*0.84).toFixed(3)})`;
  let html = '<div class="hh"></div>' +
    Array.from({length:24},(_,h)=>`<div class="hh">${h%6===0?h:""}</div>`).join("");
  for(let d=0; d<7; d++){
    html += `<div class="dl">${DOW[d]}</div>`;
    for(let h=0; h<24; h++){
      const v = grid[d][h];
      html += `<div class="cell" style="background:${bg(v)}" title="${DOW[d]} ${h}:00 — ${money(v)}"></div>`;
    }
  }
  $("#heat").innerHTML = html;
}

// Export the currently-filtered turns as CSV (all logged fields).
function exportCSV(){
  const rows = filtered();
  const cols = ["ts","session","project","model","turn","turn_tokens","turn_cost",
    "cum_tokens","cum_cost","context_pct","ctx_window","cache_read","cache_create",
    "five_h_pct","seven_d_pct"];
  const esc = v => { if(v==null) return ""; const s=String(v);
    return /[",\n]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s; };
  const lines = [cols.join(",")].concat(rows.map(r=>cols.map(c=>esc(r[c])).join(",")));
  const blob = new Blob([lines.join("\n")], {type:"text/csv"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "tokenscope-export.csv"; a.click();
  URL.revokeObjectURL(url);
}

let charts = {};
// Create the chart on first call; afterwards mutate its data and animate the
// transition (so a live refresh morphs from the last state instead of replaying
// the grow-from-zero entry animation each poll).
const LASTCFG = {};   // sel -> latest config, so the overlay can rebuild a chart
function draw(key, sel, config){
  LASTCFG[sel] = config;
  const c = charts[key];
  if (!c){ charts[key] = new Chart($(sel), config); return; }
  // Update IN PLACE: keep the chart and each dataset OBJECT, swapping only their
  // labels/data arrays. Replacing chart.data wholesale gives Chart.js new dataset
  // objects, which it animates from zero — preserving identity makes it tween
  // from the last frame instead.
  const nd = config.data;
  c.data.labels = nd.labels;
  nd.datasets.forEach((ds, i) => {
    if (c.data.datasets[i]) Object.assign(c.data.datasets[i], ds);
    else c.data.datasets[i] = ds;
  });
  c.data.datasets.length = nd.datasets.length;
  if (config.options) c.options = config.options;
  c.update();
}

function filtered(){
  const p = selP.value, m = selM.value;
  const from = $("#fFrom").value ? new Date($("#fFrom").value+"T00:00").getTime() : -Infinity;
  const to   = $("#fTo").value   ? new Date($("#fTo").value+"T23:59:59").getTime() : Infinity;
  return DATA.filter(r => (!p||r.project===p) && (!m||r.model===m) && r.epoch>=from && r.epoch<=to);
}

function peakWindow(rows, hours=5){
  if(!rows.length) return {peak:0,end:null};
  const span = hours*3600*1000; let lo=0, run=0, best=0, bestEnd=null;
  for(let hi=0; hi<rows.length; hi++){
    run += rows[hi].turn_cost||0;
    while(rows[hi].epoch - rows[lo].epoch > span){ run -= rows[lo].turn_cost||0; lo++; }
    if(run>best){ best=run; bestEnd=rows[hi].epoch; }
  }
  return {peak:best, end:bestEnd};
}

function render(){
  const rows = filtered();
  const totCost = rows.reduce((a,r)=>a+(r.turn_cost||0),0);
  const posTok  = rows.reduce((a,r)=>a+Math.max(0,r.turn_tokens||0),0);
  const cacheTot = rows.reduce((a,r)=>a+(r.cache_read||0)+(r.cache_create||0),0);
  const sessions = new Set(rows.map(r=>r.session)).size;
  const days = new Set(rows.map(r=>fmtDay(r.epoch))).size || 1;
  const costs = rows.map(r=>r.turn_cost||0);
  const maxTurn = costs.length?Math.max(...costs):0;
  const {peak} = peakWindow(rows);
  const kpi = [
    ["exact", money(totCost), "Total spend", "k_total"],
    ["exact", money(totCost/days), "Per day", "k_perday"],
    ["partial", toks(posTok), "Tokens added", "k_tokens"],
    ["border", toks(cacheTot), "Cache tokens", "k_cache"],
    ["border", money(peak), "Peak 5h window", "k_peak"],
    ["red", money(maxTurn), "Priciest turn", "k_priciest"],
    ["partial", money(totCost/days*7), "Proj. weekly", "k_weekly"],
    ["exact", rows.length+" / "+sessions, "Turns / sessions", "k_turns"],
  ];
  $("#kpis").innerHTML = kpi.map(([c,v,l,k])=>
    `<div class="kpi ${c} exp" data-entry="${k}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  const byDay = {};
  rows.forEach(r=>{ const d=fmtDay(r.epoch); (byDay[d]=byDay[d]||{c:0,t:0}); byDay[d].c+=r.turn_cost||0; byDay[d].t+=Math.max(0,r.turn_tokens||0); });
  const days_k = Object.keys(byDay).sort();
  draw("day", "#cDay", {type:"bar",
    data:{labels:days_k, datasets:[{label:"Spend",data:days_k.map(d=>byDay[d].c),
      backgroundColor:COL.exact, borderRadius:5}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.parsed.y)+
      "  ·  "+toks(byDay[c.label].t)+" tok"}}},
      scales:{x:GRID.x, y:{...GRID.y,ticks:{callback:v=>"$"+v}}}}});

  let run=0; const cum = rows.map(r=>({x:r.epoch, y:(run+=r.turn_cost||0)}));
  draw("cum", "#cCum", {type:"line",
    data:{datasets:[{data:cum, borderColor:COL.exact, backgroundColor:grad(COL.exact), fill:true}]},
    options:{parsing:false, plugins:{legend:{display:false},tooltip:{callbacks:{
      title:i=>new Date(i[0].parsed.x).toLocaleString(), label:c=>money(c.parsed.y)}}},
      scales:{x:{type:"linear",...GRID.x,ticks:{callback:v=>fmtDay(v).slice(5)}},
              y:{...GRID.y,ticks:{callback:v=>"$"+v}}}}});

  const byP={}; rows.forEach(r=>byP[r.project]=(byP[r.project]||0)+(r.turn_cost||0));
  const pe=Object.entries(byP).sort((a,b)=>b[1]-a[1]);
  draw("proj", "#cProj", {type:"doughnut",
    data:{labels:pe.map(e=>e[0]), datasets:[{data:pe.map(e=>e[1]),
      backgroundColor:PALETTE, borderColor:"#13171F", borderWidth:2}]},
    options:{cutout:"62%", interaction:{mode:"nearest",intersect:true},
      plugins:{zoom:{zoom:{wheel:{enabled:false},drag:{enabled:false}}},
      legend:{position:"right",labels:{boxWidth:10,boxHeight:10,font:{size:11},padding:10}},
      tooltip:{mode:"nearest",intersect:true,callbacks:{label:c=>c.label+": "+money(c.parsed)}}}}});

  // by model (only rows that recorded a model — older rows predate that field)
  const byM={}; rows.forEach(r=>{ if(r.model) byM[r.model]=(byM[r.model]||0)+(r.turn_cost||0); });
  const me=Object.entries(byM).sort((a,b)=>b[1]-a[1]);
  draw("model", "#cModel", {type:"doughnut",
    data:{labels:me.map(e=>modelShort(e[0])), datasets:[{data:me.map(e=>e[1]),
      backgroundColor:PALETTE, borderColor:"#13171F", borderWidth:2}]},
    options:{cutout:"62%", interaction:{mode:"nearest",intersect:true},
      plugins:{zoom:{zoom:{wheel:{enabled:false},drag:{enabled:false}}},
      legend:{position:"right",labels:{boxWidth:10,boxHeight:10,font:{size:11},padding:10}},
      tooltip:{mode:"nearest",intersect:true,callbacks:{label:c=>c.label+": "+money(c.parsed)}}}}});

  // cache tokens per day — read vs write, stacked (the bulk of traffic)
  const byDayC={};
  rows.forEach(r=>{ const d=fmtDay(r.epoch); const o=(byDayC[d]=byDayC[d]||{rd:0,wr:0});
    o.rd+=r.cache_read||0; o.wr+=r.cache_create||0; });
  const ck=Object.keys(byDayC).sort();
  draw("cache", "#cCache", {type:"bar",
    data:{labels:ck, datasets:[
      {label:"cache read", data:ck.map(d=>byDayC[d].rd), backgroundColor:COL.border, stack:"c", borderRadius:4},
      {label:"cache write", data:ck.map(d=>byDayC[d].wr), backgroundColor:COL.partial, stack:"c", borderRadius:4}]},
    options:{plugins:{legend:{position:"top",labels:{boxWidth:10,boxHeight:10,font:{size:11}}},
      tooltip:{callbacks:{label:c=>c.dataset.label+": "+toks(c.parsed.y)}}},
      scales:{x:{stacked:true,...GRID.x},y:{stacked:true,...GRID.y,ticks:{callback:v=>toks(v)}}}}});

  // cache-hit % per day = read / (read + write)
  const hk = ck.map(d=>{ const o=byDayC[d]; const tot=o.rd+o.wr; return tot? o.rd/tot*100 : null; });
  draw("hit", "#cHit", {type:"line",
    data:{labels:ck, datasets:[{data:hk, borderColor:COL.exact, backgroundColor:grad(COL.exact),
      fill:true, spanGaps:true}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{
      label:c=>"cache hit "+(c.parsed.y==null?"—":c.parsed.y.toFixed(0)+"%")}}},
      scales:{x:GRID.x, y:{min:0,max:100,...GRID.y,ticks:{callback:v=>v+"%"}}}}});

  // context fill % over time + compaction markers (turn_tokens < 0)
  const ctxLine = rows.map(r=>({x:r.epoch, y:r.context_pct==null?null:r.context_pct}));
  const compactions = rows.filter(r=>(r.turn_tokens||0)<0).map(r=>({x:r.epoch, y:r.context_pct||0}));
  draw("ctx", "#cCtx", {type:"line",
    data:{datasets:[
      {label:"context %", data:ctxLine, borderColor:COL.border, backgroundColor:grad(COL.border),
        fill:true, spanGaps:true},
      {label:"compaction", data:compactions, type:"scatter", showLine:false,
        backgroundColor:COL.red, pointRadius:4, pointHoverRadius:6}]},
    options:{parsing:false, plugins:{legend:{position:"top",labels:{boxWidth:10,boxHeight:10,font:{size:11}}},
      tooltip:{callbacks:{title:i=>new Date(i[0].parsed.x).toLocaleString(),
        label:c=>c.dataset.label==="compaction"?"compaction @ "+c.parsed.y+"%":"context "+c.parsed.y+"%"}}},
      scales:{x:{type:"linear",...GRID.x,ticks:{callback:v=>fmtDay(v).slice(5)}},
              y:{min:0,max:100,...GRID.y,ticks:{callback:v=>v+"%"}}}}});

  renderHeatmap(rows);

  const span=5*3600*1000; let lo=0,r5=0; const roll=[];
  for(let hi=0;hi<rows.length;hi++){ r5+=rows[hi].turn_cost||0;
    while(rows[hi].epoch-rows[lo].epoch>span){ r5-=rows[lo].turn_cost||0; lo++; }
    roll.push({x:rows[hi].epoch, y:r5}); }
  draw("roll", "#cRoll", {type:"line",
    data:{datasets:[{data:roll, borderColor:COL.border, backgroundColor:grad(COL.border), fill:true}]},
    options:{parsing:false, plugins:{legend:{display:false},tooltip:{callbacks:{
      title:i=>new Date(i[0].parsed.x).toLocaleString(),
      label:c=>money(c.parsed.y)+" in trailing 5h"}}},
      scales:{x:{type:"linear",...GRID.x,ticks:{callback:v=>fmtDay(v).slice(5)}},
              y:{...GRID.y,ticks:{callback:v=>"$"+v}}}}});

  // rate-limit burn over time — 5h / 7d %, only rows that carry the fields
  const rl = rows.filter(r=>r.five_h_pct!=null || r.seven_d_pct!=null);
  draw("limits", "#cLimits", {type:"line",
    data:{datasets:[
      {label:"5h %", data:rl.map(r=>({x:r.epoch,y:r.five_h_pct??null})),
        borderColor:COL.partial, fill:false, spanGaps:true},
      {label:"7d %", data:rl.map(r=>({x:r.epoch,y:r.seven_d_pct??null})),
        borderColor:COL.red, fill:false, spanGaps:true}]},
    options:{parsing:false, plugins:{legend:{position:"top",labels:{boxWidth:10,boxHeight:10,font:{size:11}}},
      tooltip:{callbacks:{title:i=>new Date(i[0].parsed.x).toLocaleString(),
        label:c=>c.dataset.label+" "+c.parsed.y+"%"}}},
      scales:{x:{type:"linear",...GRID.x,ticks:{callback:v=>fmtDay(v).slice(5)}},
              y:{min:0,max:100,...GRID.y,ticks:{callback:v=>v+"%"}}}}});

  // color encodes time: older turns → blue, recent → accent green (rows are time-sorted)
  const tmin = rows.length?rows[0].epoch:0, tspan = (rows.length?rows[rows.length-1].epoch:1)-tmin || 1;
  const C_OLD=[110,139,255], C_NEW=[62,207,142];
  const tcolor = t => { const k=Math.min(1,Math.max(0,(t-tmin)/tspan));
    return `rgba(${Math.round(C_OLD[0]+(C_NEW[0]-C_OLD[0])*k)},${Math.round(C_OLD[1]+(C_NEW[1]-C_OLD[1])*k)},${Math.round(C_OLD[2]+(C_NEW[2]-C_OLD[2])*k)},.65)`; };
  const sdata = rows.map(r=>({x:Math.max(0,r.turn_tokens||0), y:r.turn_cost||0, p:r.project, t:r.epoch}));
  draw("scatter", "#cScatter", {type:"scatter",
    data:{datasets:[{data:sdata, backgroundColor:sdata.map(d=>tcolor(d.t)), pointRadius:3, pointHoverRadius:5}]},
    options:{interaction:{mode:"nearest",intersect:true},
      plugins:{legend:{display:false},tooltip:{mode:"nearest",intersect:true,callbacks:{
      title:i=>new Date(i[0].raw.t).toLocaleString(),
      label:c=>money(c.raw.y)+" · "+toks(c.raw.x)+" tok · "+c.raw.p}}},
      scales:{x:{...GRID.x,title:{display:true,text:"main-loop tokens",color:"#5B6470"},ticks:{callback:v=>toks(v)}},
              y:{...GRID.y,title:{display:true,text:"cost",color:"#5B6470"},ticks:{callback:v=>"$"+v}}}}});

  const top=[...rows].sort((a,b)=>(b.turn_cost||0)-(a.turn_cost||0)).slice(0,12);
  $("#tTop tbody").innerHTML = top.map(r=>{
    const d=new Date(r.epoch);
    return `<tr><td>${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}</td>`+
      `<td>${(r.project||"").slice(0,16)}</td>`+
      `<td>${modelShort(r.model)}</td>`+
      `<td class="n">${money(r.turn_cost||0)}</td>`+
      `<td class="n">${toks(r.turn_tokens||0)}</td>`+
      `<td class="n">${toks((r.cache_read||0)+(r.cache_create||0))}</td>`+
      `<td class="n">${r.context_pct??""}%</td></tr>`;
  }).join("");
}

[selP,selM,$("#fFrom"),$("#fTo")].forEach(el=>el.addEventListener("change",render));
$("#exportBtn").addEventListener("click",exportCSV);

// Mode-aware labels: in serve mode the panels really are live; the static file isn't.
const modeTxt = LIVE ? `(live · every ${Math.round(POLL_MS/1000)}s)` : "(snapshot at generation)";
$("#sessMode").textContent = modeTxt;
$("#liveMode").textContent = modeTxt;
if (LIVE){
  $("#sessNote").innerHTML = "Live — polled from the running <code>tokenscope serve</code>.";
} else {
  $("#sessNote").innerHTML = "Static snapshot — for a live view run <code>tokenscope serve</code> (or <code>tokenscope grid</code> in a terminal).";
}

// ---- theme (dark / light / yellowish) ----
const THEMES=["dark","light","yellowish"];
const cssVar=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function applyChartTheme(){
  // charts read these at construction; re-run + re-render() to retheme them.
  Chart.defaults.color=cssVar('--faint');
  Chart.defaults.borderColor=cssVar('--line');
  Chart.defaults.plugins.tooltip.backgroundColor=cssVar('--card-2');
  Chart.defaults.plugins.tooltip.borderColor=cssVar('--line-2');
  Chart.defaults.plugins.tooltip.titleColor=cssVar('--txt');
  Chart.defaults.plugins.tooltip.bodyColor=cssVar('--dim');
  GRID.y.grid.color=cssVar('--line');
}
function setTheme(t){
  if(!THEMES.includes(t))t="dark";
  document.documentElement.dataset.theme=t;
  try{localStorage.setItem("ts-theme",t);}catch(e){}
  $("#themeSel").value=t; applyChartTheme();
}
(function initTheme(){
  let t="dark"; try{t=localStorage.getItem("ts-theme")||"dark";}catch(e){}
  if(!THEMES.includes(t))t="dark";
  document.documentElement.dataset.theme=t; $("#themeSel").value=t;
})();
$("#themeSel").addEventListener("change",e=>{setTheme(e.target.value);render();});

// ---- notification sounds ----
const SOUNDS=["Glass","Ping","Hero","Tink","Submarine","Pop","Sosumi","Funk","Bottle","Blow","Frog","Morse","Purr","Basso","none"];
const fillSounds=sel=>{sel.innerHTML=SOUNDS.map(s=>`<option value="${s}">${s==="none"?"(silent)":s}</option>`).join("");};
fillSounds($("#sIdle")); fillSounds($("#sNeed"));
let ALARM={master:true,volume:0.8,events:{idle:{enabled:true,sound:"Glass"},needs_input:{enabled:true,sound:"Ping"}}};
function paintAlarm(){
  $("#mMaster").checked=!!ALARM.master;
  const vp=Math.round((ALARM.volume??0.8)*100); $("#vVol").value=vp; $("#vVolN").textContent=vp+"%";
  $("#eIdle").checked=!!ALARM.events.idle.enabled; $("#sIdle").value=ALARM.events.idle.sound;
  $("#eNeed").checked=!!ALARM.events.needs_input.enabled; $("#sNeed").value=ALARM.events.needs_input.sound;
  $("#alertsBtn").classList.toggle("on",!!ALARM.master);
}
const readAlarmUI=()=>({master:$("#mMaster").checked, volume:(+$("#vVol").value||0)/100,
  events:{idle:{enabled:$("#eIdle").checked,sound:$("#sIdle").value},
          needs_input:{enabled:$("#eNeed").checked,sound:$("#sNeed").value}}});
async function saveAlarm(){
  ALARM=readAlarmUI(); paintAlarm();
  try{await fetch("alarm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(ALARM)});}catch(e){}
}
$("#vVol").addEventListener("input",()=>$("#vVolN").textContent=$("#vVol").value+"%"); // live label
if(LIVE){
  ["mMaster","eIdle","sIdle","eNeed","sNeed","vVol"].forEach(id=>$("#"+id).addEventListener("change",saveAlarm));
  fetch("alarm",{cache:"no-store"}).then(r=>r.json()).then(c=>{ALARM=c;paintAlarm();}).catch(paintAlarm);
}else{
  // A static file:// page can't persist settings — show view-only.
  document.querySelectorAll("#alertsBody input,#alertsBody select").forEach(el=>el.disabled=true);
  const n=$("#alertsNote"); n.style.display="block";
  n.innerHTML="Settings are read/written by <code>tokenscope serve</code>; this static export is view-only.";
  paintAlarm();
}

// ---- chart inspection options (global, re-render on change) ----
let CHARTOPTS={points:false,exact:false,gridx:false};
try{Object.assign(CHARTOPTS,JSON.parse(localStorage.getItem("ts-chartopts")||"{}"));}catch(e){}
function applyChartOpts(){
  Chart.defaults.elements.point.radius=CHARTOPTS.points?3:0;
  Chart.defaults.elements.line.tension=CHARTOPTS.exact?0:0.32;
  GRID.x.grid.display=!!CHARTOPTS.gridx;
}
const paintChartOpts=()=>{$("#oPoints").checked=CHARTOPTS.points;$("#oExact").checked=CHARTOPTS.exact;$("#oGrid").checked=CHARTOPTS.gridx;};
function saveChartOpts(){
  CHARTOPTS={points:$("#oPoints").checked,exact:$("#oExact").checked,gridx:$("#oGrid").checked};
  try{localStorage.setItem("ts-chartopts",JSON.stringify(CHARTOPTS));}catch(e){}
  applyChartOpts(); render();
}
["oPoints","oExact","oGrid"].forEach(id=>$("#"+id).addEventListener("change",saveChartOpts));
paintChartOpts();

// ---- per-entry detail: click a KPI / chart title → select (highlight) + overlay ----
const ENTRY = {
  k_total:{tag:"Overview · metric", body:`
    <h4>What it is</h4>Sum of every turn's cost in the selected range — <b>includes subagent / workflow spend</b> (turn_cost is the delta of the session's cumulative cost).
    <h4>Insight</h4>Your real outlay. Compare across date ranges or projects to see where the money actually goes.
    <h4>Caveat</h4>Claude Code's client-side <b>estimate</b>, not your invoice.`},
  k_perday:{tag:"Overview · metric", body:`
    <h4>What it is</h4>Total spend ÷ active days (days with at least one turn).
    <h4>Insight</h4>Your steady daily burn rate — the baseline for budgeting and for spotting abnormally heavy days.`},
  k_tokens:{tag:"Overview · metric", body:`
    <h4>What it is</h4>Sum of positive per-turn main-loop token deltas (input+output). <b>Excludes</b> subagent tokens and cache reads/writes.
    <h4>Insight</h4>How much <i>fresh</i> context you generate. Read it next to <b>Cache tokens</b> to gauge how much is reused vs. recomputed.`},
  k_cache:{tag:"Overview · metric", body:`
    <h4>What it is</h4>cache_read + cache_create tokens across the range.
    <h4>Insight</h4>Usually the bulk of traffic and far cheaper than fresh input — a high cache-to-fresh ratio means efficient reuse.
    <h4>Watch for</h4>A low cache-hit trend means context is being re-sent cold (expensive).`},
  k_peak:{tag:"Overview · usage limit", body:`
    <h4>What it is</h4>The maximum total cost found in any rolling 5-hour window.
    <h4>Insight</h4>The closest you've run to the 5-hour subscription limit. If this approaches your plan's cap, you'll start hitting rate limits.`},
  k_priciest:{tag:"Overview · outlier", body:`
    <h4>What it is</h4>The single most expensive turn in range.
    <h4>Insight</h4>Almost always a subagent / workflow fan-out — the cost the token count can't show. Find it in <b>Top turns by cost</b>.
    <h4>Watch for</h4>One turn dominating the total.`},
  k_weekly:{tag:"Overview · projection", body:`
    <h4>What it is</h4>Per-day average × 7 — a naive weekly projection.
    <h4>Insight</h4>A quick sanity-check of trajectory against the 7-day limit.
    <h4>Caveat</h4>Not a forecast: it assumes flat usage and ignores trend.`},
  k_turns:{tag:"Overview · context", body:`
    <h4>What it is</h4>Count of turns / distinct sessions in the range.
    <h4>Insight</h4>The sample size behind every average above — a small count means those per-turn stats are noisy.`},
  day:{tag:"Spend · chart", body:`
    <h4>How to read</h4>One bar per calendar day; height = estimated cost (turn_cost includes subagents, priced via <code>PRICE</code>).
    <h4>Insight</h4>Trend and spikes — whether spend is climbing and which days dominate.
    <h4>Tip</h4>Drag across the chart to zoom a span; double-click to reset.`},
  cum:{tag:"Spend · chart", body:`
    <h4>How to read</h4>Running total over the range; the <b>slope</b> is your burn rate.
    <h4>Insight</h4>A steepening curve = accelerating spend; a flat stretch = idle time.`},
  scatter:{tag:"Spend · chart", body:`
    <h4>How to read</h4>Each point is a turn — x = main-loop tokens, y = cost, color = time (blue older → green recent).
    <h4>Insight</h4>Cost-efficiency per token. Points high on cost but low on tokens are <b>subagent turns</b> — spend the token axis can't see.
    <h4>Watch for</h4>Color drifting vertically over time = your cost-per-token changing.`},
  proj:{tag:"Spend · chart", body:`
    <h4>How to read</h4>Each slice is a project directory's share of total cost.
    <h4>Insight</h4>Where spend concentrates across your work — useful for attributing cost.`},
  model:{tag:"Spend · chart", body:`
    <h4>How to read</h4>Each slice is a model's share of cost (turns that recorded a model id).
    <h4>Insight</h4>Your model cost mix. Fable runs ~2× Opus per token, so a small Fable slice can still be large spend.`},
};
const ovl=$("#entryOvl");
const selectEl=el=>{ document.querySelectorAll(".kpi.sel,.card.sel").forEach(s=>s.classList.remove("sel")); el.classList.add("sel"); };
// Render a SEPARATE chart in the overlay from the card's stored config. (Moving the
// live canvas doesn't work: Chart.js keeps measuring its original container, so the
// moved chart stays the card's small height. A fresh instance is created with the
// overlay as its container, so it sizes to the big container correctly.)
let ovlChartInst=null;
function hideOvlChart(){
  if(ovlChartInst){ try{ovlChartInst.destroy();}catch(e){} ovlChartInst=null; }
  const oc=$("#ovlChart"); oc.classList.remove("show"); oc.innerHTML="";
}
function showOvlChart(sel){
  const cfg=LASTCFG[sel]; if(!cfg) return;
  const oc=$("#ovlChart"); oc.classList.add("show"); oc.innerHTML="<canvas></canvas>";
  // own dataset objects (fresh per-chart meta) but shared underlying data arrays
  const data={labels:cfg.data.labels, datasets:cfg.data.datasets.map(d=>Object.assign({},d))};
  const options=Object.assign({}, cfg.options, {responsive:true, maintainAspectRatio:false});
  ovlChartInst=new Chart(oc.querySelector("canvas"), {type:cfg.type, data, options});
}
function closeOvl(){ ovl.classList.remove("open"); hideOvlChart();
  document.querySelectorAll(".kpi.sel,.card.sel").forEach(el=>el.classList.remove("sel")); }
function openKpi(el){
  const info=ENTRY[el.dataset.entry]; if(!info) return;
  selectEl(el); hideOvlChart();
  $("#ovlTitle").textContent=el.querySelector(".l").textContent;
  $("#ovlTag").textContent=info.tag;
  $("#ovlVal").textContent=el.querySelector(".v").textContent; $("#ovlVal").style.display="block";
  $("#ovlBody").innerHTML=info.body;
  ovl.classList.add("open");
}
function openChartCard(card){
  const canvas=card.querySelector("canvas"); if(!canvas) return;
  selectEl(card);
  const h2=card.querySelector("h2");
  const info = (card.dataset.entry && ENTRY[card.dataset.entry]) ? ENTRY[card.dataset.entry]
             : {tag:"Chart", body:(h2&&h2.dataset.desc)?`<h4>About</h4>${h2.dataset.desc}`:""};
  $("#ovlTitle").textContent = h2 ? h2.textContent.trim() : "Chart";
  $("#ovlTag").textContent = info.tag;
  $("#ovlVal").style.display="none";
  $("#ovlBody").innerHTML = info.body;
  ovl.classList.add("open");      // open FIRST so the chart container has real dimensions
  showOvlChart("#"+canvas.id);    // fresh, fully-interactive chart sized to the overlay
}
// Add a top-right ⤢ expand button to every chart card.
document.querySelectorAll(".card").forEach(card=>{
  if(!card.querySelector("canvas")) return;
  const b=document.createElement("button");
  b.className="expand-btn"; b.textContent="⤢"; b.title="Expand — graph + details";
  b.addEventListener("click",e=>{ e.stopPropagation(); openChartCard(card); });
  card.appendChild(b);
});
document.addEventListener("click",e=>{
  if(!e.target.closest) return;
  const kpi=e.target.closest(".kpi.exp[data-entry]");
  if(kpi){ openKpi(kpi); return; }
  const h2=e.target.closest("h2.exp");
  if(h2){ const card=h2.closest(".card"); if(card&&card.querySelector("canvas")) openChartCard(card); }
});
$("#ovlClose").addEventListener("click",closeOvl);
ovl.addEventListener("click",e=>{ if(e.target===ovl) closeOvl(); });
document.addEventListener("keydown",e=>{ if(e.key==="Escape") closeOvl(); });

// ---- popovers (alerts + chart) — one open at a time, click-outside closes ----
function wirePop(btn,panel){
  $(btn).addEventListener("click",e=>{e.stopPropagation();
    const el=$(panel), was=el.classList.contains("open");
    document.querySelectorAll(".pop-panel").forEach(p=>p.classList.remove("open"));
    if(!was)el.classList.add("open");});
  $(panel).addEventListener("click",e=>e.stopPropagation());
}
wirePop("#alertsBtn","#alertsPanel"); wirePop("#chartBtn","#chartPanel");
document.addEventListener("click",()=>document.querySelectorAll(".pop-panel").forEach(p=>p.classList.remove("open")));

function boot(){ applyChartOpts(); applyChartTheme(); renderLive(); populateProjects(); initDates(); renderSessions(); render(); }
boot();

// "recently" threshold control (re-render sessions on change)
const rmEl=$("#recentMin");
if(rmEl){ rmEl.value=Math.round(RECENT_SECS/60);
  rmEl.addEventListener("change",()=>{ RECENT_SECS=Math.max(1,+rmEl.value||2)*60;
    try{localStorage.setItem("ts-recent",rmEl.value);}catch(e){} renderSessions(); }); }

if (LIVE){
  const badge = $("#liveBadge");
  if (badge) badge.style.display = "inline-flex";
  async function refresh(){
    try{
      const r = await fetch("data", {cache:"no-store"});
      if (!r.ok) throw new Error(r.status);
      const j = await r.json();
      DATA = j.turns; SESSIONS = j.sessions; LIVEINFO = j.live;
      renderLive(); populateProjects(); renderSessions(); render();
      if (badge) badge.classList.remove("stale");
    }catch(e){ if (badge) badge.classList.add("stale"); }
  }
  // adjustable refresh interval ("retry distance"): select drives a resettable timer
  let pollTimer=null;
  const POLLS=[["2000","2s"],["5000","5s"],["10000","10s"],["30000","30s"],["60000","1m"],["0","Off"]];
  const psel=$("#pollSel"), pwrap=$("#pollWrap");
  function applyPoll(ms){ if(pollTimer){clearInterval(pollTimer);pollTimer=null;}
    if(ms>0) pollTimer=setInterval(refresh,ms);
    try{localStorage.setItem("ts-poll",ms);}catch(e){} }
  if(psel && pwrap){
    pwrap.style.display="";
    psel.innerHTML=POLLS.map(([v,t])=>`<option value="${v}">${t}</option>`).join("");
    let saved=POLL_MS; try{const s=localStorage.getItem("ts-poll"); if(s!==null)saved=+s;}catch(e){}
    if(!POLLS.some(([v])=>+v===saved)) saved=POLL_MS;
    psel.value=String(saved);
    psel.addEventListener("change",()=>applyPoll(+psel.value));
    applyPoll(saved);
  } else { pollTimer=setInterval(refresh,POLL_MS); }
}
</script>
</body>
</html>
"""


def build_html(rows, sessions, live=False, poll_ms=5000):
    return (HTML
            .replace("__DATA__", json.dumps(rows, separators=(",", ":")))
            .replace("__SESSIONS__", json.dumps(sessions, separators=(",", ":")))
            .replace("__LIVE_INFO__", json.dumps(live_status(), separators=(",", ":")))
            .replace("__LIVE__", "true" if live else "false")
            .replace("__POLL__", str(int(poll_ms)))
            .replace("__GEN__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__N__", str(len(rows))))


def run(args):
    rows = load(args.log)
    if not rows:
        sys.exit("No turns in the log yet.")
    html = build_html(rows, session_cards(), live=False)
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Wrote {args.out}  ({len(rows)} turns)")
    if not args.no_open:
        webbrowser.open("file://" + args.out)
