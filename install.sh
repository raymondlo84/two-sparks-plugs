#!/usr/bin/env bash
# Install the Tapo energy-plugs dashboard on this machine.
# Idempotent: safe to re-run. Copies files to ~/tapo-plugs-dash,
# checks prerequisites, starts the systemd user service.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${DEST:-$HOME/tapo-plugs-dash}"
SERVICE_NAME="spark-dash.service"
UNIT_DIR="$HOME/.config/systemd/user"
CONTAINER_NAME="tapo-mcp"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- prerequisites -----------------------------------------------------------
command -v python3 >/dev/null || die "python3 not found"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' \
  || die "python3 >= 3.8 required"

# tapo-mcp container: provision if missing, start if stopped
if ! command -v docker >/dev/null; then
  die "docker not found — the tapo-mcp container must be present on this box."
fi
if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1 \
   || sg docker -c "docker inspect $CONTAINER_NAME" >/dev/null 2>&1; then
  running="$(sg docker -c "docker inspect -f '{{.State.Running}}' $CONTAINER_NAME" 2>/dev/null \
    || docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME")"
  if [ "$running" != "true" ]; then
    say "starting tapo-mcp container"
    sg docker -c "docker start $CONTAINER_NAME" 2>/dev/null || docker start "$CONTAINER_NAME"
    sleep 2
  fi
else
  say "tapo-mcp container not found — provisioning it (creds from env, a creds file, or prompts)"
  bash "$SRC/provision-tapo-mcp.sh"
fi

# --- copy files --------------------------------------------------------------
say "installing to $DEST"
mkdir -p "$DEST"
cp "$SRC/collect.py" "$SRC/server.py" "$DEST/"
[ -f "$SRC/config.json" ] && cp "$SRC/config.json" "$DEST/"

# smoke test: one full collect
say "running a test collect"
python3 - "$DEST" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import collect
snap = collect.collect_data()
n = len(snap["plugs"])
ok = sum(1 for p in snap["plugs"] if not p["error"])
print(f"     discovered {n} plug(s), {ok} reporting: "
      + ", ".join(p["name"] for p in snap["plugs"]))
if ok == 0:
    raise SystemExit("test collect: all plugs failed: "
                     + (snap["plugs"][0]["error"] if snap["plugs"] else ""))
EOF

# --- systemd user service ------------------------------------------------------
if command -v systemctl >/dev/null && systemctl --user daemon-reload >/dev/null 2>&1; then
  say "installing systemd user service $SERVICE_NAME"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/$SERVICE_NAME" <<UNIT
[Unit]
Description=Tapo energy-plugs live dashboard server (port 8123)
After=network-online.target docker.service
Wants=network-online.target

[Service]
ExecStart=/usr/bin/env python3 ${DEST}/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE_NAME"
else
  say "systemd user session not available — starting server in background"
  nohup python3 "$DEST/server.py" >> "$DEST/server.log" 2>&1 &
  echo $! > "$DEST/server.pid"
fi

sleep 2
if curl -sf "http://127.0.0.1:8123/status.json" >/dev/null 2>&1; then
  say "OK — dashboard live at http://127.0.0.1:8123/"
  echo
  echo "Next: pin it to a session dashboard. In OpenClaw chat, ask:"
  echo "  'add a full-size browser dashboard widget to my board at http://127.0.0.1:8123/'"
  echo " (or manually: dashboard widget_put name=spark-plugs-live"
  echo "  pluginKind=browser:dashboard props={url:http://127.0.0.1:8123/} size=full)"
  echo
  echo "From another machine on the LAN: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8123/"
else
  die "server did not come up on port 8123 — check: journalctl --user -u $SERVICE_NAME -n 40"
fi
