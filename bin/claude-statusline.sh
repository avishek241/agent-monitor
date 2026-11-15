#!/bin/bash
# Claude Code status line that also feeds agent-monitor with this account's usage (rate limits).
# In <CLAUDE_CONFIG_DIR>/settings.json: "statusLine": {"type": "command", "command": "~/path/agent-monitor/bin/claude-statusline.sh /tmp/claude-status-<account>.json"}
# and point that account's "statusFile" in your agent-monitor config at the same path.
out="${1:-/tmp/agent-monitor-claude-status.json}"
input=$(cat)
printf '%s' "$input" > "$out.$$" && mv "$out.$$" "$out"
model=$(printf '%s' "$input" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('model',{}).get('display_name',''))" 2>/dev/null)
five=$(printf '%s' "$input" | python3 -c "import json,sys;d=json.load(sys.stdin);print(int(((d.get('rate_limits') or {}).get('five_hour') or {}).get('used_percentage') or 0))" 2>/dev/null)
printf '%s · 5h %s%%' "$model" "${five:-?}"
