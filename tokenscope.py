#!/usr/bin/env python3
"""tokenscope — unified Claude Code token statistics & optimization toolkit.

One entrypoint over a shared core (tokcore.py):

  tokenscope live        live single-session monitor (default — bare `tokenscope`)
  tokenscope grid        live view of ALL open sessions at once
  tokenscope report      historical CLI analysis of turn-log.jsonl  (was tokstats)
  tokenscope dashboard   static interactive HTML dashboard          (was tokstats-dash)
  tokenscope serve       live HTML dashboard (local server, auto-refreshing)

Backward compatible: bare `tokenscope` (with the old -i/-c/-f/--project flags)
still launches the live monitor.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tokcore import TURN_LOG  # noqa: E402

SUBCOMMANDS = {"live", "grid", "report", "dashboard", "serve", "graph"}


def build_parser():
    ap = argparse.ArgumentParser(prog="tokenscope", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p_live = sub.add_parser("live", help="live single-session monitor")
    p_live.add_argument("-i", "--interval", type=float, default=3, help="refresh seconds")
    p_live.add_argument("-f", "--file", help="pin a specific transcript .jsonl")
    p_live.add_argument("--project", help="restrict auto-pick to this project dir")
    p_live.add_argument("-c", "--columns", type=int, default=1, choices=[1, 2],
                        help="1 = stacked (default), 2 = side-by-side")

    p_grid = sub.add_parser("grid", help="live view of all open sessions")
    p_grid.add_argument("-i", "--interval", type=float, default=3, help="refresh seconds")
    p_grid.add_argument("-w", "--window", type=int, default=0,
                        help="only show sessions updated within N seconds "
                             "(default 0 = all sessions with a live process)")

    p_rep = sub.add_parser("report", help="historical turn-log analysis")
    p_rep.add_argument("--days", type=int, help="only the last N days")
    p_rep.add_argument("--project", help="filter to one project")
    p_rep.add_argument("--top", type=int, default=10, help="N most expensive turns")
    p_rep.add_argument("--log", default=TURN_LOG, help="path to turn-log.jsonl")

    p_dash = sub.add_parser("dashboard", help="generate interactive HTML dashboard")
    p_dash.add_argument("--log", default=TURN_LOG)
    p_dash.add_argument("--out", default=os.path.expanduser("~/.claude/tokstats-dashboard.html"))
    p_dash.add_argument("--no-open", action="store_true")

    p_srv = sub.add_parser("serve", help="live auto-refreshing HTML dashboard (local server)")
    p_srv.add_argument("--log", default=TURN_LOG)
    p_srv.add_argument("--port", type=int, default=8765)
    p_srv.add_argument("--host", default="127.0.0.1", help="bind address (default localhost only)")
    p_srv.add_argument("-i", "--interval", type=float, default=5, help="browser poll seconds")
    p_srv.add_argument("--no-open", action="store_true")

    p_graph = sub.add_parser("graph", help="live provenance graph of one session (browser)")
    p_graph.add_argument("--log", default=TURN_LOG)
    p_graph.add_argument("--port", type=int, default=8765)
    p_graph.add_argument("--host", default="127.0.0.1", help="bind address (default localhost only)")
    p_graph.add_argument("-i", "--interval", type=float, default=3, help="browser poll seconds")
    p_graph.add_argument("--no-open", action="store_true")
    return ap


def main():
    argv = sys.argv[1:]
    # Backward compat: bare `tokenscope` or `tokenscope -i 2` → live monitor.
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["live"] + argv

    args = build_parser().parse_args(argv)
    if args.cmd == "grid":
        import grid
        grid.run(args)
    elif args.cmd == "report":
        import report
        report.run(args)
    elif args.cmd == "dashboard":
        import dashboard
        dashboard.run(args)
    elif args.cmd == "serve":
        import serve
        serve.run(args)
    elif args.cmd == "graph":
        import serve
        args.open_path = "/graph"
        serve.run(args)
    else:
        import live
        live.run(args)


if __name__ == "__main__":
    main()
