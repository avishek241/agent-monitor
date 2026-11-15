#!/bin/bash
# Build (when Panel.swift changed) and open the floating panel for one config. Close the window to stop it.
#   ./start.sh [config.json]      (default: $AGENT_MONITOR_CONFIG or ~/.config/agent-monitor/config.json)
set -e
dir="$(cd "$(dirname "$0")" && pwd)"; bin="$HOME/.local/bin/agent-monitor-panel"
cfg="${1:-${AGENT_MONITOR_CONFIG:-$HOME/.config/agent-monitor/config.json}}"
cfg="$(cd "$(dirname "$cfg")" && pwd)/$(basename "$cfg")"
[ -f "$cfg" ] || { echo "No config at $cfg — copy examples/minimal.config.json there and edit it."; exit 1; }
mkdir -p "$(dirname "$bin")"
[ "$dir/Panel.swift" -nt "$bin" ] && swiftc -O "$dir/Panel.swift" -o "$bin"
pkill -f "$bin" 2>/dev/null || true
AGENT_MONITOR_CONFIG="$cfg" nohup "$bin" "$dir/status.py" >/dev/null 2>&1 &
echo "agent-monitor started with $cfg (close its window to stop)."
