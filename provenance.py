#!/usr/bin/env python3
"""provenance — turn a session transcript into a live decision/finding graph.

The 'whole picture' of a task as reasoning provenance: nodes are the prompts,
decisions and findings a session produced; edges are the derivation that led to
them (temporal chain + shared-evidence cross-links). Tool calls and touched
files are NOT nodes — they ride along as evidence attributes on the finding they
produced, so the graph stays a web of *thinking*, not a trace of mechanics.

Served live by `tokenscope graph` (see serve.py): the page polls /graph-data and
fades in new nodes as the session works.

Heuristic, and honest about it: finding extraction is marker-based, not model
judgement. It will miss subtle conclusions and occasionally promote a non-finding.
Treat the graph as a high-recall sketch of the reasoning, not ground truth.
"""
import json
import os
import re

from tokcore import discover_sessions

# --- finding extraction ---------------------------------------------------

# Lines that open with one of these read as a decision/finding, not narration.
_MARKERS = re.compile(
    r"^\s*(?:found|the issue|the problem|root cause|turns out|so\b|therefore|"
    r"confirmed|verified|fixed|the fix|decision|conclusion|i'll|i will|let me|"
    r"plan:|the bug|it's|this is because|because|the cause|key insight|"
    r"the answer|in short|short answer|bottom line)\b",
    re.IGNORECASE,
)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_WS = re.compile(r"\s+")


def _clip(s, n=90):
    s = _WS.sub(" ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _short(s, words=3):
    """A ≤3-word node label. The full text lives in the click popup, so the label
    just needs to be a recognizable handle, not the whole finding."""
    toks = _WS.sub(" ", s).strip().split(" ")
    label = " ".join(toks[:words])
    return label + "…" if len(toks) > words else label


def _tool_evidence(tu):
    name = tu.get("name", "?")
    inp = tu.get("input") or {}
    for k in ("file_path", "path"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return {"tool": name, "target": os.path.basename(v), "file": os.path.basename(v)}
    for k in ("pattern", "query", "command", "url", "description"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return {"tool": name, "target": _clip(v, 60), "file": None}
    return {"tool": name, "target": "", "file": None}


def _findings_from_text(text):
    """Pull candidate findings from one assistant text block. Bold fragments and
    marker-led lines are strong; otherwise the first sentence is a soft 'step'."""
    out = []
    for m in _BOLD.finditer(text):
        frag = m.group(1).strip()
        if len(frag) >= 4 and not frag.startswith("`"):
            out.append(("decision", frag))
    for line in text.splitlines():
        line = line.strip().lstrip("-*0123456789. )")
        if len(line) < 8:
            continue
        if _MARKERS.match(line):
            out.append(("finding", line))
    if not out:
        first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
        if len(first) >= 12:
            out.append(("step", first))
    # dedup preserving order, cap per turn so a long answer can't flood the graph
    seen, uniq = set(), []
    for kind, frag in out:
        key = _clip(frag, 60).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append((kind, _clip(frag)))
        if len(uniq) >= 3:
            break
    return uniq


# --- graph assembly --------------------------------------------------------

_cache = {}  # path -> (mtime, graph)


def build_graph(path):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]

    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return None

    nodes, edges = [], []
    prev_id = None            # tail of the derivation chain
    file_last = {}            # file basename -> last node id that touched it
    nid = 0
    turn = 0
    # evidence accrues across messages and is flushed into the next finding —
    # the tools/files that informed a conclusion usually run *before* it, often
    # in earlier turns, so the buffer must outlive a single message.
    pending_ev, pending_files = [], []

    for o in rows:
        typ = o.get("type")
        msg = o.get("message") or {}
        content = msg.get("content")

        if typ == "user":
            text = None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
                    continue  # tool result, not a real prompt
                for x in content:
                    if isinstance(x, dict) and x.get("type") == "text":
                        text = x.get("text")
                        break
            if not text or text.lstrip().startswith("<"):
                continue
            turn += 1
            nid += 1
            nodes.append({"id": str(nid), "type": "prompt", "label": _short(text),
                          "full": _clip(text, 600), "turn": turn, "evidence": []})
            if prev_id:
                edges.append({"source": prev_id, "target": str(nid), "kind": "next"})
            prev_id = str(nid)

        elif typ == "assistant" and isinstance(content, list):
            for x in content:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "tool_use":
                    ev = _tool_evidence(x)
                    pending_ev.append(ev)
                    if ev["file"]:
                        pending_files.append(ev["file"])
                elif x.get("type") == "text" and x.get("text", "").strip():
                    for kind, frag in _findings_from_text(x["text"]):
                        nid += 1
                        node = {"id": str(nid), "type": kind, "label": _short(frag),
                                "full": _clip(x["text"], 600), "turn": turn,
                                "evidence": pending_ev[-8:]}
                        nodes.append(node)
                        if prev_id:
                            edges.append({"source": prev_id, "target": str(nid), "kind": "then"})
                        # shared-evidence cross-links → the connectionist web
                        for f in set(pending_files):
                            if f in file_last and file_last[f] != str(nid):
                                edges.append({"source": file_last[f], "target": str(nid),
                                              "kind": "shares", "via": f})
                            file_last[f] = str(nid)
                        prev_id = str(nid)
                        pending_ev, pending_files = [], []  # consumed by this finding

    graph = {"nodes": nodes, "edges": edges,
             "stats": {"nodes": len(nodes), "edges": len(edges), "turns": turn}}
    _cache[path] = (mtime, graph)
    return graph


# --- session discovery for the selector ------------------------------------

def live_sessions():
    out = []
    for s in discover_sessions(max_age=0):
        tp = s.get("transcript_path") or s.get("_transcript_path")
        if not tp or not os.path.exists(tp):
            continue
        out.append({
            "id": s.get("session_id", ""),
            "name": s.get("session_name") or s.get("session_id", "")[:8],
            "project": os.path.basename((s.get("workspace") or {}).get("current_dir", "") or ""),
            "status": s.get("_status", ""),
            "age": int(s.get("_age", 0)),
            "transcript_path": tp,
        })
    return out


def graph_for_session(session_id=None):
    sessions = live_sessions()
    if not sessions:
        return {"nodes": [], "edges": [], "stats": {}, "session": None, "sessions": []}
    chosen = next((s for s in sessions if s["id"] == session_id), sessions[0])
    g = build_graph(chosen["transcript_path"]) or {"nodes": [], "edges": [], "stats": {}}
    g = dict(g)
    g["session"] = {k: chosen[k] for k in ("id", "name", "project", "status", "age")}
    g["sessions"] = [{k: s[k] for k in ("id", "name", "project", "status", "age")} for s in sessions]
    return g


# --- live graph page -------------------------------------------------------

_GRAPH_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>tokenscope · provenance</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js"></script>
<style>
  :root{--bg:#0e1116;--panel:#171c24;--edge:#3a4452;--txt:#e6edf3;--dim:#8b97a7;
        --prompt:#7c5cff;--decision:#ffb454;--finding:#3fb950;--step:#3a6ea5;--live:#ff5c8a;}
  *{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--bg);
     color:var(--txt);font:13px/1.45 -apple-system,Inter,system-ui,sans-serif}
  #bar{position:fixed;top:0;left:0;right:0;height:46px;display:flex;align-items:center;
       gap:14px;padding:0 16px;background:var(--panel);border-bottom:1px solid var(--edge);z-index:5}
  #bar b{color:#fff} #bar .dim{color:var(--dim)}
  select,button{background:#0e1116;color:var(--txt);border:1px solid var(--edge);border-radius:6px;
         padding:5px 8px;font:inherit;cursor:pointer}
  button:hover{border-color:var(--prompt)} button{min-width:30px}
  #cy{cursor:grab} #cy:active{cursor:grabbing}
  #dot{width:9px;height:9px;border-radius:50%;background:var(--dim);display:inline-block}
  #dot.live{background:var(--live);box-shadow:0 0 0 0 var(--live);animation:pulse 1.6s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(255,92,138,.6)}70%{box-shadow:0 0 0 8px rgba(255,92,138,0)}100%{box-shadow:0 0 0 0 rgba(255,92,138,0)}}
  #cy{position:fixed;top:46px;left:0;right:340px;bottom:0}
  #side{position:fixed;top:46px;right:0;bottom:0;width:340px;background:var(--panel);
        border-left:1px solid var(--edge);padding:16px;overflow:auto}
  #side h3{margin:0 0 6px;font-size:13px} #side .kind{font-size:11px;text-transform:uppercase;
        letter-spacing:.06em;color:var(--dim)} #side .full{margin:10px 0;white-space:pre-wrap}
  .ev{border-top:1px solid var(--edge);padding:7px 0;font-size:12px}
  .ev .t{color:var(--decision)} .ev .f{color:var(--finding)}
  .legend{position:fixed;bottom:10px;left:14px;font-size:11px;color:var(--dim);z-index:5}
  .legend span{margin-right:12px} .sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:middle}
</style></head><body>
<div id="bar">
  <b>tokenscope · provenance</b>
  <span id="dot"></span>
  <select id="sel"></select>
  <span class="dim" id="stat"></span>
  <span style="margin-left:auto"></span>
  <button id="mode" title="toggle layout: force-directed web ↔ chronological timeline (l)">layout: web</button>
  <button id="fit" title="fit graph to view (f)">⤢ fit</button>
  <button id="zin" title="zoom in (+)">+</button>
  <button id="zout" title="zoom out (-)">−</button>
  <span class="dim" id="refresh"></span>
</div>
<div id="cy"></div>
<div id="side"><span class="dim">Click a node to see the finding and the evidence that produced it.</span></div>
<div class="legend">
  <span><i class="sw" style="background:var(--prompt)"></i>prompt</span>
  <span><i class="sw" style="background:var(--decision)"></i>decision</span>
  <span><i class="sw" style="background:var(--finding)"></i>finding</span>
  <span><i class="sw" style="background:var(--step)"></i>step</span>
  <span style="color:var(--live)">━ derivation · ┄ shared evidence</span>
</div>
<script>
const POLL = __POLL_MS__;
let cy, current = new URLSearchParams(location.search).get('session') || '';
let needFit = true;   // fit the viewport on first load and after a session switch
let mode = localStorage.getItem('ts-graph-mode') || 'web';   // 'web' | 'timeline'
const COLOR = {prompt:'#7c5cff',decision:'#ffb454',finding:'#3fb950',step:'#3a6ea5'};

cy = cytoscape({container:document.getElementById('cy'), wheelSensitivity:.25,
  minZoom:0.08, maxZoom:3, boxSelectionEnabled:false,
  style:[
    {selector:'node',style:{'background-color':d=>COLOR[d.data('type')]||'#3a6ea5',
      'label':'data(label)','color':'#e6edf3','font-size':'11px','text-wrap':'wrap',
      'text-max-width':'170px','text-valign':'center','text-halign':'right','text-margin-x':7,
      'min-zoomed-font-size':7,'width':16,'height':16,'border-width':0}},
    {selector:'node.live',style:{'border-width':3,'border-color':'#ff5c8a','width':18,'height':18}},
    {selector:'edge',style:{'width':1.4,'line-color':'#3a4452','target-arrow-color':'#3a4452',
      'target-arrow-shape':'triangle','curve-style':'bezier','arrow-scale':.8}},
    {selector:'edge[kind="shares"]',style:{'line-style':'dashed','line-color':'#ffb45455',
      'target-arrow-shape':'none'}},
    {selector:'node:selected',style:{'border-width':3,'border-color':'#fff'}},
  ]});

cy.on('tap','node',e=>showSide(e.target.data()));
function fit(){ if(cy.elements().length) cy.animate({fit:{padding:50}},{duration:300}); }
document.getElementById('fit').onclick=fit;

// Two layouts over the same graph:
//  web      — force-directed (cose): folds the near-linear chain into a compact 2D web
//  timeline — chronological: prompts form a left spine, findings branch right, ordered
//             by creation so you read the task top-to-bottom and scroll through time.
function relayout(refit){
  if(mode==='timeline'){
    const ns = cy.nodes().sort((a,b)=>(+a.id())-(+b.id()));
    ns.forEach((n,i)=>n.position({x:(n.data('type')==='prompt')?0:280, y:i*66}));
    if(refit) cy.viewport({zoom:0.9, pan:{x:210, y:46}});  // first node near top-left
  } else {
    cy.layout({name:'cose',animate:false,randomize:refit,idealEdgeLength:110,
               nodeRepulsion:12000,nodeOverlap:30,gravity:0.25,numIter:1200,
               componentSpacing:120,fit:false}).run();
    if(refit) cy.fit(undefined,60);
  }
}
function setMode(m){
  mode=m; localStorage.setItem('ts-graph-mode',m);
  document.getElementById('mode').textContent = 'layout: '+m;
  relayout(true);
}
document.getElementById('mode').textContent = 'layout: '+mode;
document.getElementById('mode').onclick=()=>setMode(mode==='web'?'timeline':'web');
document.getElementById('zin').onclick=()=>cy.zoom({level:cy.zoom()*1.3,renderedPosition:{x:cy.width()/2,y:cy.height()/2}});
document.getElementById('zout').onclick=()=>cy.zoom({level:cy.zoom()/1.3,renderedPosition:{x:cy.width()/2,y:cy.height()/2}});
cy.on('tap',e=>{ if(e.target===cy){} });  // background tap: no-op (panning is drag)
window.addEventListener('keydown',e=>{ if(e.key==='f'){fit();} if(e.key==='l'){setMode(mode==='web'?'timeline':'web');} });
function showSide(d){
  const ev=(d.evidence||[]).map(e=>`<div class="ev"><span class="t">${e.tool}</span> `+
    `${e.file?`<span class="f">${e.file}</span>`:`<span class="dim">${e.target||''}</span>`}</div>`).join('');
  document.getElementById('side').innerHTML =
    `<div class="kind">${d.type} · turn ${d.turn}</div><h3>${esc(d.label)}</h3>`+
    `<div class="full">${esc(d.full||'')}</div>`+
    (ev?`<div class="kind">evidence</div>${ev}`:`<div class="dim">no tool evidence on this node</div>`);
}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

async function tick(){
  let g; try{ g = await (await fetch('/graph-data'+(current?`?session=${current}`:''))).json(); }
  catch(e){ document.getElementById('stat').textContent='(server gone)'; return; }
  renderSel(g.sessions, g.session);
  const live = g.session && (g.session.status==='busy'||g.session.status==='waiting');
  document.getElementById('dot').className = live?'live':'';
  document.getElementById('stat').textContent =
    g.session ? `${g.session.project} · ${g.stats.nodes||0} nodes · ${g.stats.edges||0} edges` : 'no live session';
  document.getElementById('refresh').textContent = `↻ ${POLL/1000}s`;
  apply(g);
}
function renderSel(sessions, sess){
  const sel=document.getElementById('sel');
  if(!sessions) return;
  const sig=sessions.map(s=>s.id).join(',');
  if(sel.dataset.sig!==sig){
    sel.dataset.sig=sig;
    sel.innerHTML=sessions.map(s=>`<option value="${s.id}">${esc(s.name)} · ${esc(s.project)}</option>`).join('');
    sel.onchange=()=>{current=sel.value; cy.elements().remove(); needFit=true; tick();};
  }
  if(sess) sel.value=sess.id, current=sess.id;
}
function apply(g){
  const want=new Set(g.nodes.map(n=>n.id));
  cy.nodes().forEach(n=>{ if(!want.has(n.id())) n.remove(); });
  let added=false;
  g.nodes.forEach(n=>{
    if(cy.getElementById(n.id).empty()){
      cy.add({group:'nodes',data:{id:n.id,type:n.type,label:n.label,full:n.full,turn:n.turn,evidence:n.evidence}});
      added=true;
    }
  });
  const seen=new Set(cy.edges().map(e=>e.id()));
  g.edges.forEach(e=>{ const id=`${e.source}->${e.target}:${e.kind}`;
    if(!seen.has(id) && !cy.getElementById(e.source).empty() && !cy.getElementById(e.target).empty())
      cy.add({group:'edges',data:{id,source:e.source,target:e.target,kind:e.kind}}); });
  // mark the newest node live
  cy.nodes().removeClass('live');
  if(g.nodes.length) cy.getElementById(g.nodes[g.nodes.length-1].id).addClass('live');
  if(added){
    // Lay out via the active mode. We control fit ourselves (only on first load /
    // session switch) so new nodes never yank your pan/zoom.
    relayout(needFit);
    needFit=false;
  }
}
tick(); setInterval(tick, POLL);
</script></body></html>"""


def build_graph_html(poll_ms=3000):
    return _GRAPH_HTML.replace("__POLL_MS__", str(int(poll_ms)))
