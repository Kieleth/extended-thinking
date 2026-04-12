#!/bin/bash
# EXTENDED-THINKING SAVE HOOK. Auto-sync every N exchanges.
#
# Claude Code / OpenCode / Codex "Stop" hook. After every assistant response:
# 1. Counts human messages in the session transcript
# 2. Every SAVE_INTERVAL messages, BLOCKS the AI from stopping
# 3. Returns a reason telling the AI to sync + synthesize to ET
# 4. AI does the work (et_sync + optional et_insight)
# 5. Next Stop fires with stop_hook_active=true → lets AI stop normally
#
# Skips during automated permission modes (acceptEdits / auto / bypassPermissions)
# so rapid-fire edits do not get interrupted.
#
# === INSTALL ===
# Claude Code (~/.claude/settings.json):
#
#   "hooks": {
#     "Stop": [{
#       "matcher": "*",
#       "hooks": [{
#         "type": "command",
#         "command": "/absolute/path/to/et_save_hook.sh",
#         "timeout": 30
#       }]
#     }]
#   }
#
# Codex CLI (.codex/hooks.json):
#
#   "Stop": [{
#     "type": "command",
#     "command": "/absolute/path/to/et_save_hook.sh",
#     "timeout": 30
#   }]
#
# === CONFIGURATION ===

SAVE_INTERVAL=25
STATE_DIR="$HOME/.extended-thinking/hook_state"
mkdir -p "$STATE_DIR"

# Read JSON input from stdin.
INPUT=$(cat)

# Parse session fields in one Python call. Shell-safe quoting.
eval $(echo "$INPUT" | python3 -c "
import sys, json, re
data = json.load(sys.stdin)
safe = lambda s: re.sub(r'[^a-zA-Z0-9_/.\-~]', '', str(s))
print(f'SESSION_ID=\"{safe(data.get(\"session_id\", \"unknown\"))}\"')
print(f'STOP_HOOK_ACTIVE=\"{data.get(\"stop_hook_active\", False)}\"')
print(f'TRANSCRIPT_PATH=\"{safe(data.get(\"transcript_path\", \"\"))}\"')
print(f'PERMISSION_MODE=\"{safe(data.get(\"permission_mode\", \"\"))}\"')
" 2>/dev/null)

TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~/$HOME}"

# Infinite-loop prevention: if we already blocked once for this Stop, let it through.
if [ "$STOP_HOOK_ACTIVE" = "True" ] || [ "$STOP_HOOK_ACTIVE" = "true" ]; then
    echo "{}"
    exit 0
fi

# Skip during automated modes. Blocking here would kill rapid-edit workflows.
if [ "$PERMISSION_MODE" = "acceptEdits" ] || [ "$PERMISSION_MODE" = "auto" ] || [ "$PERMISSION_MODE" = "bypassPermissions" ]; then
    echo "[$(date '+%H:%M:%S')] Session $SESSION_ID: skipped save (mode=$PERMISSION_MODE)" >> "$STATE_DIR/hook.log"
    echo "{}"
    exit 0
fi

# Count human messages in the JSONL transcript. Pass path as argv to avoid injection.
if [ -f "$TRANSCRIPT_PATH" ]; then
    EXCHANGE_COUNT=$(python3 - "$TRANSCRIPT_PATH" <<'PYEOF'
import json, sys
count = 0
with open(sys.argv[1]) as f:
    for line in f:
        try:
            entry = json.loads(line)
            msg = entry.get('message', {})
            if isinstance(msg, dict) and msg.get('role') == 'user':
                content = msg.get('content', '')
                if isinstance(content, str) and '<command-message>' in content:
                    continue
                count += 1
        except Exception:
            pass
print(count)
PYEOF
2>/dev/null)
else
    EXCHANGE_COUNT=0
fi

LAST_SAVE_FILE="$STATE_DIR/${SESSION_ID}_last_save"
LAST_SAVE=0
if [ -f "$LAST_SAVE_FILE" ]; then
    LAST_SAVE=$(cat "$LAST_SAVE_FILE")
fi

SINCE_LAST=$((EXCHANGE_COUNT - LAST_SAVE))

echo "[$(date '+%H:%M:%S')] Session $SESSION_ID: $EXCHANGE_COUNT exchanges, $SINCE_LAST since last sync" >> "$STATE_DIR/hook.log"

if [ "$SINCE_LAST" -ge "$SAVE_INTERVAL" ] && [ "$EXCHANGE_COUNT" -gt 0 ]; then
    echo "$EXCHANGE_COUNT" > "$LAST_SAVE_FILE"
    echo "[$(date '+%H:%M:%S')] TRIGGERING SYNC at exchange $EXCHANGE_COUNT" >> "$STATE_DIR/hook.log"

    cat << 'HOOKJSON'
{
  "decision": "block",
  "reason": "Extended-thinking sync checkpoint. Call the et_sync MCP tool to pull recent memory chunks into the concept store. If significant patterns have emerged this session, also call et_insight to synthesize. Continue the conversation after the tool calls complete."
}
HOOKJSON
else
    echo "{}"
fi
