#!/usr/bin/env python3
"""serve — live HTML dashboard backed by a tiny local HTTP server.

Unlike `tokenscope dashboard` (a static file://), this serves the page and a
`/data` JSON endpoint the page polls, so charts and the Active-sessions panel
update on an interval with no regeneration. Binds to localhost only — the page
carries your usage/cost data, so it is never exposed on the network.
"""
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dashboard
from tokcore import TURN_LOG


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
    url = f"http://{args.host}:{args.port}/"
    print(f"tokenscope dashboard live at {url}  (refresh every {args.interval}s)")
    print("Ctrl-C to stop.")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserver stopped.")
        httpd.shutdown()
