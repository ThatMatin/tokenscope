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
    box-shadow:var(--shadow)}
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
  .ctxbar{display:inline-block;height:6px;border-radius:3px;background:var(--accent);vertical-align:middle}
  .ctxtrack{display:inline-block;width:90px;height:6px;border-radius:3px;background:var(--line-2);vertical-align:middle;overflow:hidden}
  .pill{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--accent);margin-right:6px;vertical-align:middle;
    box-shadow:0 0 6px color-mix(in srgb,var(--accent) 70%,transparent)}
  .pill.idle{background:var(--faint);box-shadow:none}
  .muted{color:var(--faint)}
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
  </div>
</header>
<div class="wrap">
  <div class="section">Live</div>
  <div class="card full" id="liveCard" style="margin-bottom:16px;display:none">
    <h2 data-desc="Current 5h/7d subscription rate-limit usage, the daily budget synthesized from the 7-day window, and rtk proxy savings. Account-wide, from the latest turn.">Usage limits &amp; budget <span id="liveMode" style="text-transform:none;font-weight:400"></span></h2>
    <div id="liveBody" class="livegrid"></div>
  </div>
  <div class="card full" id="sessCard" style="display:none">
    <h2 data-desc="Every Claude Code session with a live process — joined from the session registry (names, liveness) and per-session snapshots (cost, context). Cache hit & in:out are aggregated from each transcript.">Active sessions <span id="sessMode" style="text-transform:none;font-weight:400"></span></h2>
    <table id="tSess"><thead><tr><th></th><th>Session</th><th>Project</th><th>Model</th><th class="n">Context</th><th class="n">Cache hit</th><th class="n">In:out</th><th class="n">Cost</th><th class="n">Active</th></tr></thead><tbody></tbody></table>
    <div class="sess-note" id="sessNote"></div>
  </div>

  <div class="section">Overview <span class="n">· selected range</span></div>
  <div class="kpis" id="kpis"></div>

  <div class="section">Spend</div>
  <div class="grid">
    <div class="card full"><h2 data-desc="Estimated cost per calendar day. turn_cost includes subagent spend; the estimate uses the PRICE rates in tokcore.py.">Spend per day</h2><canvas id="cDay"></canvas></div>
    <div class="card"><h2 data-desc="Running total of cost across the selected range.">Cumulative spend</h2><canvas id="cCum"></canvas></div>
    <div class="card"><h2 data-desc="Each point is one turn: main-loop tokens added (x) vs its cost (y). Hover for the project.">Cost vs. tokens per turn</h2><canvas id="cScatter"></canvas></div>
    <div class="card"><h2 data-desc="Share of total cost by project directory.">Spend by project</h2><canvas id="cProj"></canvas></div>
    <div class="card"><h2 data-desc="Share of total cost by model. Only turns that recorded a model id (newer rows) are counted.">Spend by model</h2><canvas id="cModel"></canvas></div>
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

function renderSessions(){
  const card = $("#sessCard");
  if (!SESSIONS.length){ card.style.display = "none"; return; }
  card.style.display = "";
  $("#tSess tbody").innerHTML = SESSIONS.map(s=>{
    const ctxc = s.ctx<60?COL.exact:s.ctx<85?COL.partial:COL.red;
    const ctxCell = s.has_snapshot
      ? `<span class="ctxtrack"><span class="ctxbar" style="width:${Math.min(100,s.ctx)}%;background:${ctxc}"></span></span> ${s.ctx}%`
      : `<span class="muted">no turn yet</span>`;
    const costCell = s.has_snapshot ? money(s.cost) : "—";
    const hitCell = s.cache_hit!=null ? (s.cache_hit*100).toFixed(0)+"%" : '<span class="muted">—</span>';
    const ioCell  = s.io_ratio ? s.io_ratio.toFixed(0)+":1" : '<span class="muted">—</span>';
    const cls = (s.status==="busy"||s.status==="waiting") ? "" : "idle";
    return `<tr><td><span class="pill ${cls}"></span></td>`+
      `<td>${s.name}</td><td>${s.project}</td><td>${s.has_snapshot?s.model:"?"}</td>`+
      `<td class="n">${ctxCell}</td>`+
      `<td class="n">${hitCell}</td><td class="n">${ioCell}</td>`+
      `<td class="n">${costCell}</td><td class="n">${fmtAge(s.age)} ago</td></tr>`;
  }).join("");
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
function draw(key, sel, config){
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
    ["exact", money(totCost), "Total spend"],
    ["exact", money(totCost/days), "Per day"],
    ["partial", toks(posTok), "Tokens added"],
    ["border", toks(cacheTot), "Cache tokens"],
    ["border", money(peak), "Peak 5h window"],
    ["red", money(maxTurn), "Priciest turn"],
    ["partial", money(totCost/days*7), "Proj. weekly"],
    ["exact", rows.length+" / "+sessions, "Turns / sessions"],
  ];
  $("#kpis").innerHTML = kpi.map(([c,v,l])=>
    `<div class="kpi ${c}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

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
    options:{cutout:"62%", plugins:{legend:{position:"right",labels:{boxWidth:10,boxHeight:10,font:{size:11},padding:10}},
      tooltip:{callbacks:{label:c=>c.label+": "+money(c.parsed)}}}}});

  // by model (only rows that recorded a model — older rows predate that field)
  const byM={}; rows.forEach(r=>{ if(r.model) byM[r.model]=(byM[r.model]||0)+(r.turn_cost||0); });
  const me=Object.entries(byM).sort((a,b)=>b[1]-a[1]);
  draw("model", "#cModel", {type:"doughnut",
    data:{labels:me.map(e=>modelShort(e[0])), datasets:[{data:me.map(e=>e[1]),
      backgroundColor:PALETTE, borderColor:"#13171F", borderWidth:2}]},
    options:{cutout:"62%", plugins:{legend:{position:"right",labels:{boxWidth:10,boxHeight:10,font:{size:11},padding:10}},
      tooltip:{callbacks:{label:c=>c.label+": "+money(c.parsed)}}}}});

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

  draw("scatter", "#cScatter", {type:"scatter",
    data:{datasets:[{data:rows.map(r=>({x:Math.max(0,r.turn_tokens||0), y:r.turn_cost||0, p:r.project})),
      backgroundColor:"rgba(110,139,255,.5)", pointRadius:3, pointHoverRadius:5}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{
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

function boot(){ renderLive(); populateProjects(); initDates(); renderSessions(); render(); }
boot();

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
  setInterval(refresh, POLL_MS);
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
