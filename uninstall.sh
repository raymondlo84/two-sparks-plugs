#!/usr/bin/env bash
# Remove the Tapo energy-plugs dashboard from this machine.
set -euo pipefail

SERVICE_NAME="spark-dash.service"
DEST="$HOME/tapo-plugs-dash"

echo "Stopping $SERVICE_NAME..."
systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/$SERVICE_NAME"
systemctl --user daemon-reload 2>/dev/null || true

# fall back to pid file if no systemd
[ -f "$DEST/server.pid" ] && kill "$(cat "$DEST/server.pid")" 2>/dev/null || true

rm -rf "$DEST"
echo "Removed $DEST and the service. (tapo-mcp container untouched.)"
