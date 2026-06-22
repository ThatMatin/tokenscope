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
#
# Ring ONLY for live, interactive sessions — via a session allowlist.
#
# Claude Code fires this Stop/Notification hook for every TOP-LEVEL session,
# including headless, cron, and cloud-routine runs (typically out of /tmp). Those
# would each ding even though no human is watching them. The hook payload carries
# NO field that distinguishes them (verified: Stop/Notification payloads never
# include `agent_id` — the old guard keyed on that and was dead code).
#
# The reliable signal: statusline.sh writes ~/.claude/tokscope-sessions/{id}.json
# on every render, and the statusline renders ONLY in interactive sessions. So a
# session_id with a snapshot file == an interactive session the user is watching;
# headless/cron sessions never create one. (Task sub-agents fire SubagentStop,
# which this script isn't wired to, so they're already silent regardless.)
event="${1:-idle}"
stdin_json=$(cat 2>/dev/null)
if [ -n "$stdin_json" ] && command -v jq >/dev/null 2>&1; then
  sid=$(printf '%s' "$stdin_json" | jq -r '.session_id // empty' 2>/dev/null)
  # No session id, or no statusline snapshot for it → not interactive → silent.
  { [ -z "$sid" ] || [ ! -f "$HOME/.claude/tokscope-sessions/${sid}.json" ]; } && exit 0
fi
cfg="$HOME/.claude/tokenscope-alarm.json"
{ [ -f "$cfg" ] && command -v jq >/dev/null 2>&1; } || exit 0
[ "$(jq -r '.master // true' "$cfg" 2>/dev/null)" = "true" ] || exit 0
[ "$(jq -r --arg e "$event" '.events[$e].enabled // false' "$cfg" 2>/dev/null)" = "true" ] || exit 0
sound=$(jq -r --arg e "$event" '.events[$e].sound // "Glass"' "$cfg" 2>/dev/null)
{ [ -z "$sound" ] || [ "$sound" = "none" ]; } && exit 0
vol=$(jq -r '.volume // 1' "$cfg" 2>/dev/null)   # 0.0–1.0 gain for afplay -v
case "$vol" in ""|*[!0-9.]*) vol=1;; esac

f="/System/Library/Sounds/${sound}.aiff"
if command -v afplay >/dev/null 2>&1 && [ -f "$f" ]; then
  ( afplay -v "$vol" "$f" >/dev/null 2>&1 & )   # background so the hook returns instantly
else
  printf '\a' > /dev/tty 2>/dev/null            # terminal-bell fallback (non-macOS)
fi
exit 0
