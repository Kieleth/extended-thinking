#!/bin/bash
# EXTENDED-THINKING PRE-COMPACT HOOK. Emergency sync before compaction.
#
# Claude Code "PreCompact" hook. Fires RIGHT BEFORE the conversation is
# compressed to free up context. This is the safety net.
#
# Unlike the save hook (every N exchanges), this ALWAYS blocks because
# compaction always loses context worth preserving.
#
# === INSTALL ===
# Claude Code (~/.claude/settings.json):
#
#   "hooks": {
#     "PreCompact": [{
#       "hooks": [{
#         "type": "command",
#         "command": "/absolute/path/to/et_precompact_hook.sh",
#         "timeout": 30
#       }]
#     }]
#   }

STATE_DIR="$HOME/.extended-thinking/hook_state"
mkdir -p "$STATE_DIR"

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | python3 -c "
import sys, json, re
safe = lambda s: re.sub(r'[^a-zA-Z0-9_/.\-~]', '', str(s))
print(safe(json.load(sys.stdin).get('session_id', 'unknown')))
" 2>/dev/null)

echo "[$(date '+%H:%M:%S')] PRE-COMPACT triggered for session $SESSION_ID" >> "$STATE_DIR/hook.log"

cat << 'HOOKJSON'
{
  "decision": "block",
  "reason": "Compaction imminent. Call et_sync to pull all recent memory chunks, then et_insight to synthesize patterns. After compaction, detailed context will be lost; the synthesized concepts in the ET graph persist. Be thorough with the sync, then allow compaction to proceed."
}
HOOKJSON
