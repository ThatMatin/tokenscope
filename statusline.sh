#!/bin/bash
# tokenscope statusline — a compact, visualized Claude Code status line.
#
# Renders two lines:
#   1. model · dir · context bar · tokens (Δ/turn) · cost (Δ/turn) · elapsed
#   2. /usage rate limits (5h + 7d bars with reset countdowns) · optional rtk savings
#
# Side effects (both consumed by the tokenscope dashboard):
#   • ~/.claude/usage-snapshot.json  — the raw payload, so the dashboard can read
#     the /usage rate limits + authoritative cost (it has no stdin of its own).
#   • ~/.claude/turn-log.jsonl       — one record per completed turn (see README).
#
# Wire it up in ~/.claude/settings.json:
#   { "statusLine": { "type": "command", "command": "~/.claude/statusline.sh" } }
#
# Requires: jq, python3 (python3 only for the optional rtk segment).
set -o pipefail
input=$(cat)

# --- parse the payload ---
MODEL=$(echo "$input"     | jq -r '.model.display_name // "Claude"')
MODEL_ID=$(echo "$input"  | jq -r '.model.id // "unknown"')
PCT=$(echo "$input"       | jq -r '.context_window.used_percentage // 0')
PCT_INT=$(printf "%.0f" "$PCT")
IN_TOK=$(echo "$input"    | jq -r '.context_window.total_input_tokens // 0')
OUT_TOK=$(echo "$input"   | jq -r '.context_window.total_output_tokens // 0')
CTX_SIZE=$(echo "$input"  | jq -r '.context_window.context_window_size // 0')
CACHE_READ=$(echo "$input"   | jq -r '.context_window.current_usage.cache_read_input_tokens // 0')
CACHE_CREATE=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')
COST=$(echo "$input"      | jq -r '.cost.total_cost_usd // 0')
DURATION_MS=$(echo "$input" | jq -r '.cost.total_duration_ms // 0')
DIR=$(echo "$input"       | jq -r '.workspace.current_dir // ""' | xargs basename 2>/dev/null)
SESSION_ID=$(echo "$input" | jq -r '.session_id // ""')
CURRENT_DIR=$(echo "$input" | jq -r '.workspace.current_dir // ""')
# Use the transcript path the harness provides directly. Reconstructing it from
# current_dir breaks when the cwd differs from the session's launch dir (e.g.
# after a cd), which silently zeroes out the per-turn deltas.
TRANSCRIPT=$(echo "$input" | jq -r '.transcript_path // ""')
# /usage rate limits — present only after the first API turn, subscription plans.
FIVE_PCT=$(echo "$input"  | jq -r '.rate_limits.five_hour.used_percentage // empty')
FIVE_RESET=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
SEVEN_PCT=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
SEVEN_RESET=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')

TOTAL_TOK=$((IN_TOK + OUT_TOK))
MINS=$((DURATION_MS / 60000)); SECS=$(((DURATION_MS % 60000) / 1000))
[ "$MINS" -gt 0 ] && TIME_STR="${MINS}m ${SECS}s" || TIME_STR="${SECS}s"
COST_STR=$(printf "\$%.4f" "$COST")

# --- colors ---
EXACT=$'\033[38;5;114m'      # reported directly by the harness
PARTIAL=$'\033[38;5;215m'    # true but incomplete (excludes subagents/cache)
GENERATED=$'\033[38;5;245m'  # derived / annotation
RESET=$'\033[0m'
SEP="${RESET} · "

# mkbar PCT WIDTH -> colored block bar (green<50 / amber<80 / red>=80)
mkbar() {
  local pct=${1%%.*} w=${2:-10}; [ -z "$pct" ] && pct=0
  local filled=$(( (pct * w + 50) / 100 ))
  [ "$filled" -gt "$w" ] && filled=$w; [ "$filled" -lt 0 ] && filled=0
  local empty=$((w - filled)) color i bar=""
  if [ "$pct" -lt 50 ]; then color=$'\033[38;5;114m'
  elif [ "$pct" -lt 80 ]; then color=$'\033[38;5;215m'
  else color=$'\033[38;5;203m'; fi
  for ((i=0;i<filled;i++)); do bar+="█"; done
  for ((i=0;i<empty;i++)); do bar+="░"; done
  printf '%s%s%s' "$color" "$bar" "$RESET"
}
# fmt_reset EPOCH -> compact "2h9m" / "5d3h" countdown
fmt_reset() {
  local r=${1%%.*} now d; now=$(date +%s); d=$((r - now))
  [ "$d" -lt 0 ] && { echo "now"; return; }
  if [ "$d" -ge 86400 ]; then echo "$((d/86400))d$(((d%86400)/3600))h"
  elif [ "$d" -ge 3600 ]; then echo "$((d/3600))h$(((d%3600)/60))m"
  else echo "$((d/60))m"; fi
}

# Snapshot the full payload for the dashboard (which has no stdin of its own).
printf '%s' "$input" > "$HOME/.claude/usage-snapshot.json" 2>/dev/null
# Per-session snapshot (keyed by session id) so `tokenscope grid` can show
# authoritative cost/context per open session, joined to Claude Code's own
# session registry. Separate dir from ~/.claude/sessions (that's the harness's).
if [ -n "$SESSION_ID" ]; then
  mkdir -p "$HOME/.claude/tokscope-sessions" 2>/dev/null
  printf '%s' "$input" > "$HOME/.claude/tokscope-sessions/${SESSION_ID}.json" 2>/dev/null
fi

# Optional rtk savings: never call rtk synchronously (too slow for a statusline).
# Cache holds "EPOCH SAVED PCT"; refresh in the background when stale (>60s).
RTK_CACHE="$HOME/.claude/rtk-cache.txt"
RTK_TS=0; RTK_SAVED=""; RTK_PCT=""
[ -f "$RTK_CACHE" ] && read -r RTK_TS RTK_SAVED RTK_PCT < "$RTK_CACHE"
if command -v rtk >/dev/null 2>&1 && { [ -z "$RTK_TS" ] || [ $(( $(date +%s) - ${RTK_TS:-0} )) -gt 60 ]; }; then
  ( OUT=$(rtk gain --format json 2>/dev/null | python3 -c "import sys,json;s=json.load(sys.stdin)['summary'];print(f\"{s['total_saved']/1e6:.2f}M {s['avg_savings_pct']:.0f}\")" 2>/dev/null); [ -n "$OUT" ] && echo "$(date +%s) $OUT" > "$RTK_CACHE" ) &>/dev/null &
  disown $! 2>/dev/null
fi

# --- per-turn deltas + enriched turn log ---
# Snapshot cumulative totals when a new user prompt appears; delta = current - baseline.
DELTA_STR=""
if [ -n "$TRANSCRIPT" ] && [ -n "$SESSION_ID" ]; then
  DELTA_STATE="${TRANSCRIPT%.jsonl}.tokdelta"
  if [ -f "$TRANSCRIPT" ]; then
    # tool_result rows are also type:user — exclude so the baseline resets only on real prompts.
    USER_COUNT=$(grep '"type":"user"' "$TRANSCRIPT" 2>/dev/null | grep -vc 'tool_result')
    PREV_COUNT=0; BASELINE=0; COST_BASELINE=0
    [ -f "$DELTA_STATE" ] && read -r PREV_COUNT BASELINE COST_BASELINE < "$DELTA_STATE"
    if [ "$USER_COUNT" -gt "${PREV_COUNT:-0}" ]; then
      if [ "${PREV_COUNT:-0}" -gt 0 ]; then
        FIN_TOK=$((TOTAL_TOK - ${BASELINE:-0}))
        FIN_COST=$(awk -v c="$COST" -v b="${COST_BASELINE:-0}" 'BEGIN{d=c-b;if(d<0)d=0;printf "%.4f",d}')
        if [ "$FIN_TOK" -gt 0 ] || [ "$FIN_COST" != "0.0000" ]; then
          # Enriched record: also logs model, window size, cache volume, and rate-limit
          # burn — signals a bare input+output delta would miss.
          printf '{"ts":"%s","session":"%s","project":"%s","turn":%s,"turn_tokens":%s,"turn_cost":%s,"cum_tokens":%s,"cum_cost":%s,"context_pct":%s,"model":"%s","ctx_window":%s,"cache_read":%s,"cache_create":%s,"five_h_pct":%s,"seven_d_pct":%s}\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SESSION_ID" "$DIR" "$PREV_COUNT" \
            "$FIN_TOK" "$FIN_COST" "$TOTAL_TOK" "$COST" "$PCT_INT" \
            "$MODEL_ID" "$CTX_SIZE" "$CACHE_READ" "$CACHE_CREATE" "${FIVE_PCT:-0}" "${SEVEN_PCT:-0}" \
            >> "$HOME/.claude/turn-log.jsonl"
        fi
      fi
      BASELINE=$TOTAL_TOK; COST_BASELINE=$COST
      echo "$USER_COUNT $BASELINE $COST_BASELINE" > "$DELTA_STATE"
    fi
    DELTA=$((TOTAL_TOK - ${BASELINE:-0})); [ "$DELTA" -lt 0 ] && DELTA=0
    COST_DELTA=$(awk -v c="$COST" -v b="${COST_BASELINE:-0}" 'BEGIN{d=c-b;if(d<0)d=0;printf "%.4f",d}')
    DELTA_STR="${PARTIAL}Δ${DELTA} tok${SEP}${GENERATED}Δ\$${COST_DELTA}${RESET}"
  fi
fi

# --- line 1: model · dir · context bar · tokens (Δ) · cost · time ---
LINE="${EXACT}${MODEL}${SEP}${EXACT}${DIR}${RESET} $(mkbar "$PCT_INT" 10) ${EXACT}${PCT_INT}%${SEP}${PARTIAL}${TOTAL_TOK}t${RESET}"
[ -n "$DELTA_STR" ] && LINE="${LINE}${SEP}${DELTA_STR}"
LINE="${LINE}${SEP}${EXACT}${COST_STR}${SEP}${EXACT}${TIME_STR}${RESET}"
echo -e "$LINE"

# --- line 2: /usage rate limits + optional rtk ---
USAGE_LINE=""
if [ -n "$FIVE_PCT" ]; then
  F_INT=$(printf "%.0f" "$FIVE_PCT")
  USAGE_LINE="${EXACT}5h${RESET} $(mkbar "$F_INT" 8) ${EXACT}${F_INT}%${RESET}"
  [ -n "$FIVE_RESET" ] && USAGE_LINE="${USAGE_LINE} ${GENERATED}↻$(fmt_reset "$FIVE_RESET")${RESET}"
fi
if [ -n "$SEVEN_PCT" ]; then
  S_INT=$(printf "%.0f" "$SEVEN_PCT")
  [ -n "$USAGE_LINE" ] && USAGE_LINE="${USAGE_LINE}${SEP}"
  USAGE_LINE="${USAGE_LINE}${EXACT}7d${RESET} $(mkbar "$S_INT" 8) ${EXACT}${S_INT}%${RESET}"
  [ -n "$SEVEN_RESET" ] && USAGE_LINE="${USAGE_LINE} ${GENERATED}↻$(fmt_reset "$SEVEN_RESET")${RESET}"
fi
if [ -n "$RTK_SAVED" ]; then
  [ -n "$USAGE_LINE" ] && USAGE_LINE="${USAGE_LINE}${SEP}"
  USAGE_LINE="${USAGE_LINE}${GENERATED}rtk ↓${RTK_PCT}% ${RTK_SAVED}${RESET}"
fi
[ -n "$USAGE_LINE" ] && echo -e "$USAGE_LINE"
exit 0
