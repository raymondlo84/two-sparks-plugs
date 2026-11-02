#!/usr/bin/env bash
# Manage the tapo-mcp docker container for the energy-plugs dashboard.
#
# Usage:
#   provision-tapo-mcp.sh            provision (idempotent: existing
#                                    running container => exits 0)
#   provision-tapo-mcp.sh stop       stop the container (kept, with env)
#   provision-tapo-mcp.sh remove     stop and delete the container
#                                    (creds files are kept)
#   provision-tapo-mcp.sh status     show container + port status
#
# Credential sources, in priority order:
#   1. env vars TAPO_MCP_USERNAME / TAPO_MCP_PASSWORD / TAPO_MCP_API_KEY
#   2. a creds file (default ~/.openclaw/settings/tapo-mcp.env, or
#      TAPO_CREDS_FILE=/path) with KEY=VALUE lines
#   3. interactive prompts
#
# Discovery target (TAPO_MCP_DISCOVERY_TARGET) defaults to the broadcast
# address of the machine's default-route subnet; override with
# TAPO_MCP_DISCOVERY_TARGET=192.168.0.255.
set -euo pipefail

IMAGE="ghcr.io/mihai-dinculescu/tapo-mcp:latest"
CONTAINER="tapo-mcp"
PORT=3000
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CREDS_FILE="${TAPO_CREDS_FILE:-$HOME/.openclaw/settings/tapo-mcp.env}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# run a docker command, via the docker group when the user is not a member
dock() {
  if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    docker "$@"
  else
    local cmd
    cmd="$(printf '%q ' "$@")"
    sg docker -c "docker $cmd"
  fi
}

command -v docker >/dev/null 2>&1 || die "docker not found"

# --- stop / remove / status ---------------------------------------------------
ACTION="${1:-provision}"

container_state() {
  dock inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "absent"
}

stop_container() {
  st="$(container_state)"
  if [ "$st" = "absent" ]; then
    say "no $CONTAINER container to stop"
    return 0
  fi
  if [ "$st" = "running" ]; then
    say "stopping $CONTAINER"
    dock stop "$CONTAINER"
  else
    say "$CONTAINER is already $st"
  fi
  say "OK — $CONTAINER stopped (container and its env are kept)"
}

remove_container() {
  st="$(container_state)"
  if [ "$st" = "absent" ]; then
    say "no $CONTAINER container to remove"
    return 0
  fi
  if [ "$st" = "running" ]; then
    say "stopping $CONTAINER"
    dock stop "$CONTAINER"
  fi
  say "removing $CONTAINER"
  dock rm "$CONTAINER"
  say "OK — $CONTAINER removed (creds files kept)"
}

status_container() {
  st="$(container_state)"
  echo "container:  $CONTAINER = $st"
  if [ "$st" = "running" ]; then
    if curl -s -m 2 -o /dev/null "http://127.0.0.1:$PORT/"; then
      echo "port 3000:  responding on 127.0.0.1:$PORT"
    else
      echo "port 3000:  NOT responding (still starting? check: docker logs $CONTAINER)"
    fi
  fi
  exit 0
}

case "$ACTION" in
  stop)   stop_container;   exit 0 ;;
  remove) remove_container; exit 0 ;;
  status) status_container ;;
  provision) : ;;
  -h|--help|help)
    sed -n '2,20p' "$0"
    exit 0
    ;;
  *) die "unknown action '$ACTION' (expected: provision | stop | remove | status)" ;;
esac

command -v openssl >/dev/null 2>&1 || die "openssl not found"

# --- container exists? --------------------------------------------------------
if dock inspect "$CONTAINER" >/dev/null 2>&1; then
  state="$(dock inspect -f '{{.State.Status}}' "$CONTAINER")"
  if [ "$state" = "running" ]; then
    say "tapo-mcp container already running — nothing to do"
  else
    say "tapo-mcp container exists but is $state — starting it"
    dock start "$CONTAINER"
  fi
else
  # --- gather credentials ------------------------------------------------------
  # Candidate creds files, first existing wins:
  #   1. $TAPO_CREDS_FILE (explicit override)
  #   2. default ~/.openclaw/settings/tapo-mcp.env
  #   3. tapo-mcp.env bundled in this package dir (shipped with the install)
  load_creds_file() {
    [ -f "$CREDS_FILE" ] || return 1
    say "reading credentials from $CREDS_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$CREDS_FILE"
    set +a
  }
  if ! load_creds_file && [ -f "$SCRIPT_DIR/tapo-mcp.env" ]; then
    say "reading credentials from bundled $SCRIPT_DIR/tapo-mcp.env"
    set -a
    # shellcheck disable=SC1090
    source "$SCRIPT_DIR/tapo-mcp.env"
    set +a
  fi

  [ -n "${TAPO_MCP_USERNAME:-}" ] || {
    say "Tapo account username? "
    read -r TAPO_MCP_USERNAME
  }
  [ -n "${TAPO_MCP_PASSWORD:-}" ] || {
    say "Tapo account password? "
    read -rs TAPO_MCP_PASSWORD
    echo
  }
  [ -n "${TAPO_MCP_API_KEY:-}" ] || {
    say "no API key supplied — generating one (openssl rand -hex 32)"
    TAPO_MCP_API_KEY="$(openssl rand -hex 32)"
  }
  [ -n "$TAPO_MCP_USERNAME" ] && [ -n "$TAPO_MCP_PASSWORD" ] || die "missing Tapo credentials"

  # --- discovery target ---------------------------------------------------------
  # Broadcast of the best default route's interface subnet. Derive from the
  # route's `dev` + that interface's CIDR (NOT field $3, which is the gateway).
  if [ -z "${TAPO_MCP_DISCOVERY_TARGET:-}" ]; then
    deflt="$(ip route show default 2>/dev/null | head -1)"
    if [ -z "$deflt" ]; then
      die "could not detect the default route; set TAPO_MCP_DISCOVERY_TARGET=<broadcast>"
    fi
    dev="$(printf '%s\n' "$deflt" | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}')"
    cidr=""
    if [ -n "$dev" ]; then
      cidr="$(ip -4 -o addr show dev "$dev" 2>/dev/null | awk '$3=="inet"{print $4; exit}')"
    fi
    if [ -z "$cidr" ]; then
      die "could not determine the default-route subnet (dev=$dev); set TAPO_MCP_DISCOVERY_TARGET=<broadcast>"
    fi
    TAPO_MCP_DISCOVERY_TARGET="$(python3 - "$cidr" <<'PY'
import ipaddress, sys
print(ipaddress.ip_network(sys.argv[1], strict=False).broadcast_address)
PY
)" || die "could not compute broadcast from $cidr"
    # with multiple default routes this is the best-metric one; if the plugs
    # live on another subnet, override with TAPO_MCP_DISCOVERY_TARGET
  fi
  say "discovery target: $TAPO_MCP_DISCOVERY_TARGET (override: TAPO_MCP_DISCOVERY_TARGET=<broadcast>)"

  # --- run ----------------------------------------------------------------------
  say "creating $CONTAINER from $IMAGE"
  dock pull "$IMAGE"
  dock run -d --name "$CONTAINER" --network host \
    --restart unless-stopped \
    -e "TAPO_MCP_USERNAME=$TAPO_MCP_USERNAME" \
    -e "TAPO_MCP_PASSWORD=$TAPO_MCP_PASSWORD" \
    -e "TAPO_MCP_DISCOVERY_TARGET=$TAPO_MCP_DISCOVERY_TARGET" \
    -e "TAPO_MCP_API_KEY=$TAPO_MCP_API_KEY" \
    -e "TAPO_MCP_ALLOWED_HOSTS=127.0.0.1,localhost" \
    "$IMAGE"

  say "waiting for tapo-mcp on 127.0.0.1:$PORT ..."
  for i in $(seq 1 30); do
    if curl -sf -m 2 "http://127.0.0.1:$PORT/" -o /dev/null 2>&1 \
       || curl -s -m 2 -o /dev/null "http://127.0.0.1:$PORT/"; then
      say "tapo-mcp is up"
      break
    fi
    sleep 1
    [ "$i" = 30 ] && die "tapo-mcp did not come up; check: docker logs $CONTAINER"
  done

  # keep a creds file around so other tooling (and the dashboard's
  # tapo-mcp-control skill) can reuse it — NEVER echo values.
  dir="$(dirname "$CREDS_FILE")"
  if [ ! -f "$CREDS_FILE" ]; then
    mkdir -p "$dir"
    {
      echo "# tapo-mcp container credentials (written by provision-tapo-mcp.sh)"
      echo "TAPO_MCP_USERNAME=$TAPO_MCP_USERNAME"
      echo "TAPO_MCP_PASSWORD=$TAPO_MCP_PASSWORD"
      echo "TAPO_MCP_DISCOVERY_TARGET=$TAPO_MCP_DISCOVERY_TARGET"
      echo "TAPO_MCP_API_KEY=$TAPO_MCP_API_KEY"
    } > "$CREDS_FILE"
    chmod 600 "$CREDS_FILE"
    say "credentials saved to $CREDS_FILE (mode 600)"
  else
    say "creds file $CREDS_FILE already exists — left unchanged"
  fi
fi

say "OK — tapo-mcp ready at 127.0.0.1:$PORT"
