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
<style>
  :root{
    --bg:#0e1117; --card:#161b22; --line:#222b36; --txt:#e6edf3; --dim:#9aa0a6;
    --exact:#2eb67d; --border:#36c5f0; --partial:#ecb22e; --red:#e01e5a; --gray:#6b7280;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  header{padding:20px 28px;border-bottom:1px solid var(--line);
    display:flex;align-items:center;gap:18px;flex-wrap:wrap}
  h1{font-size:18px;margin:0;font-weight:700}
  h1 .z{color:var(--exact)}
  .controls{display:flex;gap:12px;align-items:center;margin-left:auto;flex-wrap:wrap}
  select,input{background:var(--card);color:var(--txt);border:1px solid var(--line);
    border-radius:7px;padding:6px 9px;font-size:13px}
  label{color:var(--dim);font-size:12px;margin-right:4px}
  .wrap{padding:22px 28px;max-width:1280px;margin:0 auto}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:22px}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .kpi .v{font-size:26px;font-weight:700;letter-spacing:-.5px}
  .kpi .l{color:var(--dim);font-size:12px;margin-top:3px}
  .kpi.exact .v{color:var(--exact)} .kpi.partial .v{color:var(--partial)}
  .kpi.border .v{color:var(--border)} .kpi.red .v{color:var(--red)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .card h2{font-size:13px;margin:0 0 12px;color:var(--dim);font-weight:600;
    text-transform:uppercase;letter-spacing:.5px}
  .card.full{grid-column:1/-1}
  canvas{max-height:300px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--dim);font-weight:600}
  td.n{text-align:right;font-variant-numeric:tabular-nums}
  .ctxbar{display:inline-block;height:8px;border-radius:4px;background:var(--exact);vertical-align:middle}
  .ctxtrack{display:inline-block;width:90px;height:8px;border-radius:4px;background:var(--line);vertical-align:middle;overflow:hidden}
  .pill{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--exact);margin-right:6px;vertical-align:middle}
  .pill.idle{background:var(--gray)}
  .muted{color:var(--gray)}
  .livegrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px 24px}
  .lv{display:flex;align-items:center;gap:8px;font-size:13px}
  .lv .lvl{display:inline-block;width:42px;color:var(--dim)}
  .lv b{font-variant-numeric:tabular-nums}
  #liveBadge{display:none;align-items:center;gap:6px;font-size:12px;color:var(--exact);
    border:1px solid var(--line);border-radius:20px;padding:3px 10px}
  #liveBadge::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--exact);
    animation:pulse 1.6s infinite}
  #liveBadge.stale{color:var(--partial)} #liveBadge.stale::before{background:var(--partial);animation:none}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .sess-note{color:var(--gray);font-size:11px;margin-top:8px}
  .legend{font-size:11px;color:var(--gray);margin-top:14px;line-height:1.7}
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
    <span><label>From</label><input type="date" id="fFrom"></span>
    <span><label>To</label><input type="date" id="fTo"></span>
  </div>
</header>
<div class="wrap">
  <div class="card full" id="liveCard" style="margin-bottom:22px;display:none">
    <h2>Usage limits &amp; budget <span id="liveMode" style="text-transform:none;font-weight:400"></span></h2>
    <div id="liveBody" class="livegrid"></div>
  </div>
  <div class="card full" id="sessCard" style="margin-bottom:22px;display:none">
    <h2>Active sessions <span id="sessMode" style="text-transform:none;font-weight:400"></span></h2>
    <table id="tSess"><thead><tr><th></th><th>Session</th><th>Project</th><th>Model</th><th class="n">Context</th><th class="n">Cache hit</th><th class="n">In:out</th><th class="n">Cost</th><th class="n">Active</th></tr></thead><tbody></tbody></table>
    <div class="sess-note" id="sessNote"></div>
  </div>
  <div class="kpis" id="kpis"></div>
  <div class="grid">
    <div class="card full"><h2>Spend per day</h2><canvas id="cDay"></canvas></div>
    <div class="card"><h2>Cumulative spend</h2><canvas id="cCum"></canvas></div>
    <div class="card"><h2>Spend by project</h2><canvas id="cProj"></canvas></div>
    <div class="card"><h2>Spend by model</h2><canvas id="cModel"></canvas></div>
    <div class="card"><h2>Cost vs. tokens per turn</h2><canvas id="cScatter"></canvas></div>
    <div class="card full"><h2>Cache tokens per day (read vs. write)</h2><canvas id="cCache"></canvas></div>
    <div class="card full"><h2>5-hour rolling window (usage-limit proxy)</h2><canvas id="cRoll"></canvas></div>
    <div class="card full"><h2>Rate-limit burn over time (5h / 7d %)</h2><canvas id="cLimits"></canvas></div>
    <div class="card full"><h2>Top 12 turns by cost</h2>
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
const COL = {exact:"#2eb67d", partial:"#ecb22e", border:"#36c5f0", red:"#e01e5a"};
const PALETTE = ["#2eb67d","#36c5f0","#ecb22e","#e01e5a","#9b87f5","#6b7280","#e8912d","#4cd4b0"];
// claude-opus-4-8 -> Opus 4-8 ; claude-3-5-haiku-20241022 -> 3-5-haiku ...
const modelShort = m => !m ? "?" : m.replace(/^claude-/,"").replace(/-\d{8}$/,"")
  .replace(/^(opus|sonnet|haiku)/i, s=>s[0].toUpperCase()+s.slice(1));
Chart.defaults.color = "#9aa0a6";
Chart.defaults.borderColor = "#222b36";
Chart.defaults.font.family = "-apple-system,Segoe UI,Roboto,sans-serif";

const fmtDay = e => { const d=new Date(e); return d.getFullYear()+"-"+
  String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); };
const fmtAge = s => s<90 ? s+"s" : s<5400 ? Math.round(s/60)+"m" : Math.round(s/3600)+"h";

const selP = $("#fProj");

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
    const cls = s.status==="busy" ? "" : "idle";
    return `<tr><td><span class="pill ${cls}"></span></td>`+
      `<td>${s.name}</td><td>${s.project}</td><td>${s.has_snapshot?s.model:"?"}</td>`+
      `<td class="n">${ctxCell}</td>`+
      `<td class="n">${hitCell}</td><td class="n">${ioCell}</td>`+
      `<td class="n">${costCell}</td><td class="n">${fmtAge(s.age)} ago</td></tr>`;
  }).join("");
}

// Repopulate the project filter, preserving the current selection.
function populateProjects(){
  const cur = selP.value;
  const projects = [...new Set(DATA.map(r=>r.project))].sort();
  selP.innerHTML = '<option value="">All projects</option>' +
    projects.map(p=>`<option>${p}</option>`).join("");
  if (cur && projects.includes(cur)) selP.value = cur;
}

// Default the date range once (don't clobber a user's choice on live refresh).
function initDates(){
  if (DATA.length && !$("#fFrom").value) $("#fFrom").value = fmtDay(DATA[0].epoch);
  if (DATA.length && !$("#fTo").value)   $("#fTo").value   = fmtDay(DATA[DATA.length-1].epoch);
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
  const p = selP.value;
  const from = $("#fFrom").value ? new Date($("#fFrom").value+"T00:00").getTime() : -Infinity;
  const to   = $("#fTo").value   ? new Date($("#fTo").value+"T23:59:59").getTime() : Infinity;
  return DATA.filter(r => (!p||r.project===p) && r.epoch>=from && r.epoch<=to);
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
      scales:{y:{ticks:{callback:v=>"$"+v}}}}});

  let run=0; const cum = rows.map(r=>({x:r.epoch, y:(run+=r.turn_cost||0)}));
  draw("cum", "#cCum", {type:"line",
    data:{datasets:[{data:cum, borderColor:COL.exact, backgroundColor:"rgba(46,182,125,.12)",
      fill:true, tension:.25, pointRadius:0, borderWidth:2}]},
    options:{parsing:false, plugins:{legend:{display:false},tooltip:{callbacks:{
      title:i=>new Date(i[0].parsed.x).toLocaleString(), label:c=>money(c.parsed.y)}}},
      scales:{x:{type:"linear",ticks:{callback:v=>fmtDay(v).slice(5)}},
              y:{ticks:{callback:v=>"$"+v}}}}});

  const byP={}; rows.forEach(r=>byP[r.project]=(byP[r.project]||0)+(r.turn_cost||0));
  const pe=Object.entries(byP).sort((a,b)=>b[1]-a[1]);
  draw("proj", "#cProj", {type:"doughnut",
    data:{labels:pe.map(e=>e[0]), datasets:[{data:pe.map(e=>e[1]),
      backgroundColor:PALETTE, borderColor:"#161b22", borderWidth:2}]},
    options:{plugins:{legend:{position:"right",labels:{boxWidth:11,font:{size:11}}},
      tooltip:{callbacks:{label:c=>c.label+": "+money(c.parsed)}}}}});

  // by model (only rows that recorded a model — older rows predate that field)
  const byM={}; rows.forEach(r=>{ if(r.model) byM[r.model]=(byM[r.model]||0)+(r.turn_cost||0); });
  const me=Object.entries(byM).sort((a,b)=>b[1]-a[1]);
  draw("model", "#cModel", {type:"doughnut",
    data:{labels:me.map(e=>modelShort(e[0])), datasets:[{data:me.map(e=>e[1]),
      backgroundColor:PALETTE, borderColor:"#161b22", borderWidth:2}]},
    options:{plugins:{legend:{position:"right",labels:{boxWidth:11,font:{size:11}}},
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
    options:{plugins:{legend:{position:"top",labels:{boxWidth:11,font:{size:11}}},
      tooltip:{callbacks:{label:c=>c.dataset.label+": "+toks(c.parsed.y)}}},
      scales:{x:{stacked:true},y:{stacked:true,ticks:{callback:v=>toks(v)}}}}});

  const span=5*3600*1000; let lo=0,r5=0; const roll=[];
  for(let hi=0;hi<rows.length;hi++){ r5+=rows[hi].turn_cost||0;
    while(rows[hi].epoch-rows[lo].epoch>span){ r5-=rows[lo].turn_cost||0; lo++; }
    roll.push({x:rows[hi].epoch, y:r5}); }
  draw("roll", "#cRoll", {type:"line",
    data:{datasets:[{data:roll, borderColor:COL.border, backgroundColor:"rgba(54,197,240,.10)",
      fill:true, tension:.2, pointRadius:0, borderWidth:2}]},
    options:{parsing:false, plugins:{legend:{display:false},tooltip:{callbacks:{
      title:i=>new Date(i[0].parsed.x).toLocaleString(),
      label:c=>money(c.parsed.y)+" in trailing 5h"}}},
      scales:{x:{type:"linear",ticks:{callback:v=>fmtDay(v).slice(5)}},
              y:{ticks:{callback:v=>"$"+v}}}}});

  // rate-limit burn over time — 5h / 7d %, only rows that carry the fields
  const rl = rows.filter(r=>r.five_h_pct!=null || r.seven_d_pct!=null);
  draw("limits", "#cLimits", {type:"line",
    data:{datasets:[
      {label:"5h %", data:rl.map(r=>({x:r.epoch,y:r.five_h_pct??null})),
        borderColor:COL.partial, backgroundColor:"rgba(236,178,46,.10)", fill:false,
        tension:.2, pointRadius:0, borderWidth:2, spanGaps:true},
      {label:"7d %", data:rl.map(r=>({x:r.epoch,y:r.seven_d_pct??null})),
        borderColor:COL.red, backgroundColor:"rgba(224,30,90,.10)", fill:false,
        tension:.2, pointRadius:0, borderWidth:2, spanGaps:true}]},
    options:{parsing:false, plugins:{legend:{position:"top",labels:{boxWidth:11,font:{size:11}}},
      tooltip:{callbacks:{title:i=>new Date(i[0].parsed.x).toLocaleString(),
        label:c=>c.dataset.label+" "+c.parsed.y+"%"}}},
      scales:{x:{type:"linear",ticks:{callback:v=>fmtDay(v).slice(5)}},
              y:{min:0,max:100,ticks:{callback:v=>v+"%"}}}}});

  draw("scatter", "#cScatter", {type:"scatter",
    data:{datasets:[{data:rows.map(r=>({x:Math.max(0,r.turn_tokens||0), y:r.turn_cost||0, p:r.project})),
      backgroundColor:"rgba(54,197,240,.55)", pointRadius:4}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{
      label:c=>money(c.raw.y)+" · "+toks(c.raw.x)+" tok · "+c.raw.p}}},
      scales:{x:{title:{display:true,text:"main-loop tokens"},ticks:{callback:v=>toks(v)}},
              y:{title:{display:true,text:"cost"},ticks:{callback:v=>"$"+v}}}}});

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

[selP,$("#fFrom"),$("#fTo")].forEach(el=>el.addEventListener("change",render));

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
