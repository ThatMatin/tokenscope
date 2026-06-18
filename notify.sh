#!/bin/bash
# tokenscope notify — play a per-event notification sound, driven by the
# dashboard's Notifications controls. Invoked by Claude Code hooks:
#   Stop         -> notify.sh idle         (session finished, your turn)
#   Notification -> notify.sh needs_input  (Claude is waiting on you)
#
# Config: ~/.claude/tokenscope-alarm.json (written by `tokenscope serve`):
#   { "master": true,
#     "events": { "idle": {"enabled":true,"sound":"Glass"},
#                 "needs_input": {"enabled":true,"sound":"Ping"} } }
# Absent config or jq → silent (fail-safe). sound "none" → silent.
event="${1:-idle}"
cfg="$HOME/.claude/tokenscope-alarm.json"
{ [ -f "$cfg" ] && command -v jq >/dev/null 2>&1; } || exit 0
[ "$(jq -r '.master // true' "$cfg" 2>/dev/null)" = "true" ] || exit 0
[ "$(jq -r --arg e "$event" '.events[$e].enabled // false' "$cfg" 2>/dev/null)" = "true" ] || exit 0
sound=$(jq -r --arg e "$event" '.events[$e].sound // "Glass"' "$cfg" 2>/dev/null)
{ [ -z "$sound" ] || [ "$sound" = "none" ]; } && exit 0

f="/System/Library/Sounds/${sound}.aiff"
if command -v afplay >/dev/null 2>&1 && [ -f "$f" ]; then
  ( afplay "$f" >/dev/null 2>&1 & )   # background so the hook returns instantly
else
  printf '\a' > /dev/tty 2>/dev/null  # terminal-bell fallback (non-macOS)
fi
exit 0
