#!/usr/bin/env python3
"""serve — live HTML dashboard backed by a tiny local HTTP server.

Unlike `tokenscope dashboard` (a static file://), this serves the page and a
`/data` JSON endpoint the page polls, so charts and the Active-sessions panel
update on an interval with no regeneration. Binds to localhost only — the page
carries your usage/cost data, so it is never exposed on the network.
"""
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dashboard
import provenance
from tokcore import TURN_LOG

ALARM_CFG = os.path.expanduser("~/.claude/tokenscope-alarm.json")
ALARM_DEFAULT = {
    "master": True,
    "volume": 0.8,
    "events": {
        "idle": {"enabled": True, "sound": "Glass"},
        "needs_input": {"enabled": True, "sound": "Ping"},
    },
}


def read_alarm():
    try:
        with open(ALARM_CFG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return ALARM_DEFAULT


def write_alarm(cfg):
    """Persist only known keys, coercing types — never trust the POST body blindly."""
    try:
        vol = max(0.0, min(1.0, float(cfg.get("volume", 0.8))))
    except (TypeError, ValueError):
        vol = 0.8
    safe = {"master": bool(cfg.get("master", True)), "volume": round(vol, 3), "events": {}}
    for ev in ("idle", "needs_input"):
        e = (cfg.get("events") or {}).get(ev) or {}
        safe["events"][ev] = {
            "enabled": bool(e.get("enabled", True)),
            "sound": str(e.get("sound", "Glass"))[:32],
        }
    tmp = ALARM_CFG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(safe, f, indent=2)
    os.replace(tmp, ALARM_CFG)
    return safe


def make_handler(log_path, poll_ms):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/":
                rows = dashboard.load(log_path)
                html = dashboard.build_html(rows, dashboard.session_cards(),
                                            live=True, poll_ms=poll_ms)
                self._send(html.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/data":
                payload = {"turns": dashboard.load(log_path),
                           "sessions": dashboard.session_cards(),
                           "live": dashboard.live_status()}
                self._send(json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                           "application/json")
            elif path == "/alarm":
                self._send(json.dumps(read_alarm()).encode("utf-8"),
                           "application/json")
            elif path == "/graph":
                html = provenance.build_graph_html(poll_ms)
                self._send(html.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/graph-data":
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                sid = (qs.get("session") or [None])[0]
                g = provenance.graph_for_session(sid)
                self._send(json.dumps(g, separators=(",", ":")).encode("utf-8"),
                           "application/json")
            else:
                self.send_error(404)

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/alarm":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(n) or b"{}")
                    saved = write_alarm(body)
                except (ValueError, OSError) as e:
                    self.send_error(400, str(e))
                    return
                self._send(json.dumps(saved).encode("utf-8"), "application/json")
            else:
                self.send_error(404)

        def log_message(self, *args):  # silence per-request stderr logging
            pass

    return Handler


def run(args):
    handler = make_handler(args.log, int(args.interval * 1000))
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as e:
        sys.exit(f"Cannot bind {args.host}:{args.port} — {e} "
                 f"(try a different --port).")
    open_path = getattr(args, "open_path", "/")
    base = f"http://{args.host}:{args.port}"
    label = "provenance graph" if open_path == "/graph" else "dashboard"
    print(f"tokenscope {label} live at {base}{open_path}  (refresh every {args.interval}s)")
    print("Ctrl-C to stop.")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(base + open_path)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserver stopped.")
        httpd.shutdown()
