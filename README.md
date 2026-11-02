# Tapo energy-plugs — live dashboard

A self-refreshing dashboard for Tapo energy-monitoring plugs (P110 / P110M /
P115): per-plug power cards, a live 3-hour power line chart (5-min samples),
and today's hourly energy bars. Data comes from the local `tapo-mcp` MCP
container; a tiny Python server re-collects every 30 seconds and the page
re-fetches every 20 seconds — no manual interaction, no external services.

Built and proven on a home setup with two "Nvidia Spark" P115 plugs; it
auto-discovers any energy plug tapo-mcp can see.

## What you get

```
collect.py    collector: tapo-mcp MCP handshake + DeviceInfo,
              PowerDataEvery5Minutes (3h) and EnergyDataHourly (today)
              for every energy plug (auto-discovered, or config.json)
server.py     ThreadingHTTPServer on 127.0.0.1:8123
              /            self-refreshing dashboard page
              /data.json   latest snapshot (last good kept on failure)
              /status.json uptime + collect health
install.sh    copies to ~/tapo-plugs-dash, checks prerequisites,
              provisions/starts tapo-mcp, test-collects, installs
              systemd user service, verifies
provision-tapo-mcp.sh  manage the tapo-mcp container:
              no args -> provision (idempotent: running container
              => exits 0; stopped => start; missing => pull+run with
              creds from env/creds file/prompts, auto-generated API
              key, auto-detected subnet broadcast)
              stop     stop it (container + env kept)
              remove   stop + delete it (creds files kept)
              status   container state + port 3000 check
uninstall.sh  stops + removes service and files (tapo-mcp untouched)
config.example.json  example of optional config.json (pin display
              names + device ids); copy to config.json to use
```

## Prerequisites

- `docker` on the box, and a user who can reach it (`docker` group or
  sudo equivalent).
- Tapo account username + password, supplied via env vars
  `TAPO_MCP_USERNAME` / `TAPO_MCP_PASSWORD`, a creds file
  (`TAPO_CREDS_FILE`, default `~/.openclaw/settings/tapo-mcp.env`),
  or interactive prompts. `install.sh` handles all three (it calls
  `provision-tapo-mcp.sh` when the container is missing).
- `provision-tapo-mcp.sh` runs the standard layout:
  ```
  docker run -d --name tapo-mcp --network host --restart unless-stopped \
    -e TAPO_MCP_USERNAME=<tapo user> -e TAPO_MCP_PASSWORD=*** pass> \
    -e TAPO_MCP_DISCOVERY_TARGET=<broadcast of the plug's subnet> \
    -e TAPO_MCP_API_KEY=*** generated> -e TAPO_MCP_ALLOWED_HOSTS=127.0.0.1,localhost \
    ghcr.io/mihai-dinculescu/tapo-mcp:latest
  ```
  If a `tapo-mcp` container already exists it is left as-is (started
  if stopped).

## Install

```
./install.sh
```

Non-interactive when the Tapo creds are already in
`~/.openclaw/settings/tapo-mcp.env`; otherwise it prompts for the
username and password once.

Then pin it to an OpenClaw session dashboard:

> add a full-size browser dashboard widget to my board at http://127.0.0.1:8123/

(or manually: `dashboard widget_put name=spark-plugs-live
pluginKind=browser:dashboard props={url:"http://127.0.0.1:8123/"} size=full`)

From another device on the LAN: `http://<host-ip>:8123/`.

## Notes / gotchas

- **Discovery**: the collector asks tapo-mcp for all P110/P110M/P115 devices.
  If tapo-mcp discovery returns nothing, its `TAPO_MCP_DISCOVERY_TARGET` is
  usually a stale broadcast address — recreate the container with the
  current subnet's broadcast.
- **Energy hours**: tapo reports `EnergyDataHourly` in UTC; `collect.py`
  maps to local hours with the DST-aware wall-clock offset. Don't "fix"
  this to a constant.
- **API key**: read from the live container env on each collect — never
  stored in a file here.
- **Failures**: if a collect fails the server keeps the last good snapshot
  and shows a stale banner; check `/status.json` (`fail_count`,
  `last_error`) or `journalctl --user -u spark-dash.service -n 40`.
- The dashboard only binds 127.0.0.1 in `server.py`; the LAN address works
  because the page is fetched locally by the same host's browser. If you
  serve it cross-machine, put it behind auth or a tunnel — it exposes
  energy usage, not credentials, but still.
