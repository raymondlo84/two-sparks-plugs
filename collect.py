#!/usr/bin/env python3
"""Shared collector for the Tapo energy-plugs dashboard.

collect_data() returns a fresh snapshot dict; the server reuses it every
COLLECT interval. The server keeps the last good snapshot on failure.

Plugs: config.json ({"plugs": {name: device_id}}) pins exact devices when
present; otherwise every Tapo energy-monitoring device reported by tapo-mcp
list_devices (P110, P110M, P115) is auto-discovered.
"""
import json
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
BASE = "http://127.0.0.1:3000/"

ENERGY_MODELS = {"P110", "P110M", "P115"}


def get_api_key():
    # The API key lives in the container env, not any file: read it from
    # the live container. Try plain docker first, then the docker group.
    last_err = None
    for argv in (
        ["docker", "inspect", "tapo-mcp", "--format", "{{json .Config.Env}}"],
        ["sg", "docker", "-c",
         'docker inspect tapo-mcp --format "{{json .Config.Env}}"'],
    ):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except FileNotFoundError as e:
            last_err = e
            continue
        if r.returncode == 0 and r.stdout.strip():
            env = None
            try:
                env = json.loads(r.stdout.strip())
            except Exception as e:
                raise RuntimeError(f"could not parse tapo-mcp container env: {e}")
            # {{json .Config.Env}} yields a JSON array of KEY=VALUE strings;
            # tolerate a full Config object too.
            if isinstance(env, dict):
                env = env.get("Env") or []
            for e in env or []:
                if e.startswith("TAPO_MCP_API_KEY="):
                    return e.split("=", 1)[1]
            raise RuntimeError("TAPO_MCP_API_KEY not present in tapo-mcp container env")
        last_err = r.stderr.strip()[:200] or f"exit {r.returncode}"
    raise RuntimeError(
        "tapo-mcp container not reachable (" + str(last_err) +
        "); start it with: docker start tapo-mcp")


class Mcp:
    def __init__(self, key):
        self.key = key
        self.session = None

    def post(self, payload, expect_sid=False):
        req = urllib.request.Request(BASE, data=json.dumps(payload).encode(),
                                     method="POST")
        req.add_header("Authorization", "Bearer " + self.key)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self.session:
            req.add_header("Mcp-Session-Id", self.session)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
            if expect_sid:
                self.session = r.headers.get("Mcp-Session-Id")
        return body

    def call(self, tool, args):
        result = self._sse(self.post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}))
        text = result["content"][0]["text"]
        try:
            return json.loads(text)
        except Exception:
            return text

    @staticmethod
    def _sse(body):
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                except Exception:
                    continue
                if "error" in obj:
                    raise RuntimeError(obj["error"].get("message", "tool error"))
                if "result" in obj:
                    return obj["result"]
        raise RuntimeError("no result in MCP response")

    def connect(self):
        self.post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "spark-plugs-dash", "version": "1.0"}}},
            expect_sid=True)
        if not self.session:
            raise RuntimeError("no Mcp-Session-Id returned from tapo-mcp")
        try:
            self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            pass  # 202 no-body is normal


def iso_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_plugs_config():
    """Return {device_id: name} from config.json, or {} when absent/bad."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return {str(did): name
                for name, did in (cfg.get("plugs") or {}).items()}
    except Exception:
        return {}


def read_plug(mcp, name, did, ip, now, tz_offset_min):
    """Read one plug fully; on any failure return a card with error set and
    no partial measurements (so the UI never mixes fresh power with
    stale/zero energy for the same cycle)."""
    p = {"name": name, "id": did, "ip": ip, "error": None,
         "power": [], "energy_pairs": [], "today_wh": 0,
         "on": None, "on_time": 0}
    try:
        info = mcp.call("get_device_state", {
            "id": did, "ip": ip,
            "capability": json.dumps({"type": "DeviceInfo"})})
        on = bool(info.get("device_on"))
        on_time = int(info.get("on_time") or 0)

        start = (now - timedelta(hours=3)).replace(
            minute=(now.minute // 5) * 5, second=0, microsecond=0)
        pw = mcp.call("get_device_state", {
            "id": did, "ip": ip,
            "capability": json.dumps({"type": "PowerDataEvery5Minutes",
                                      "start_date_time": iso_utc(start),
                                      "hours": 3})})
        power = [[int(datetime.strptime(e["start_date_time"],
                                        "%Y-%m-%dT%H:%M:%SZ")
                      .replace(tzinfo=timezone.utc).timestamp() * 1000),
                  int(e["power"])]
                 for e in pw.get("entries", [])]

        today = (now.astimezone(
            timezone(timedelta(minutes=tz_offset_min)))).date()
        en = mcp.call("get_device_state", {
            "id": did, "ip": ip,
            "capability": json.dumps({"type": "EnergyDataHourly",
                                      "start_date": str(today), "days": 1})})
        by_hour = {}
        for e in en.get("entries", []):
            # EnergyDataHourly returns UTC hours; map with the wall-clock
            # offset (DST-aware), not a fixed shift.
            local_hour = (datetime.strptime(e["start_date_time"],
                                            "%Y-%m-%dT%H:%M:%SZ")
                          + timedelta(minutes=tz_offset_min)).hour
            by_hour[local_hour] = int(e["energy"])
        energy_pairs = [[h, by_hour.get(h)] for h in range(24)]
        today_wh = sum(v for v in by_hour.values() if v is not None)

        # commit only after every read succeeded
        p.update({"on": on, "on_time": on_time, "power": power,
                  "energy_pairs": energy_pairs, "today_wh": today_wh})
    except Exception as e:
        p["error"] = str(e)[:300]
    return p


def collect_data():
    key = get_api_key()
    mcp = Mcp(key)
    mcp.connect()
    listing = mcp.call("list_devices", {"timeout": 20})
    devices = listing.get("devices", [])
    ip_by_id = {d["id"]: d.get("ip") for d in devices}

    # {device_id: display_name}; empty means pure auto-discovery
    cfg = parse_plugs_config()

    now = datetime.now(timezone.utc)
    tz_offset_min = round((datetime.now().replace(tzinfo=None) -
                           now.replace(tzinfo=None)).total_seconds() / 60)

    plugs = []
    if cfg:
        # Explicit pin: show exactly these devices, in config order.
        for did, name in cfg.items():
            if did in ip_by_id:
                plugs.append(read_plug(mcp, name, did, ip_by_id.get(did),
                                       now, tz_offset_min))
            else:
                plugs.append({
                    "name": name, "id": did, "ip": None,
                    "error": "not visible in tapo-mcp discovery "
                             "(wrong id, or tapo-mcp discovery target is on "
                             "the wrong subnet)",
                    "power": [], "energy_pairs": [], "today_wh": 0,
                    "on": None, "on_time": 0,
                })
    else:
        wanted = []
        for d in devices:
            if d.get("model") in ENERGY_MODELS:
                name = d.get("name") or d.get("model") or d["id"]
                wanted.append((name, d["id"]))
        if not wanted:
            raise RuntimeError(
                "no energy-monitoring plugs (P110/P110M/P115) found via "
                "tapo-mcp; check that tapo-mcp discovery sees your network "
                "(TAPO_MCP_DISCOVERY_TARGET must be a broadcast address on "
                "the plug's subnet)")
        plugs = [read_plug(mcp, name, did, ip_by_id.get(did), now,
                           tz_offset_min)
                 for name, did in wanted]

    if all(p["error"] for p in plugs):
        raise RuntimeError("all plugs failed: " + plugs[0]["error"])
    return {
        "generated_at": iso_utc(now),
        "generated_ms": int(now.timestamp() * 1000),
        "tz_offset_minutes": tz_offset_min,
        "plugs": plugs,
    }
