#!/usr/bin/env python3
"""Live server for the Tapo energy-plugs dashboard.

Serves a self-refreshing page on 127.0.0.1:8123:
  /            -> dashboard page (re-fetches /data.json every 20s)
  /data.json   -> latest snapshot (kept in memory, refreshed by collector)
  /status.json -> server health (uptime, last collect ok/failed, failures)

A daemon thread re-runs collect.collect_data() every COLLECT_EVERY_S (30s).
On failure the last good snapshot
is kept and served, and a stale banner is shown on the page.

Any number of energy plugs is supported; the page renders one card per plug
and per-plug hourly energy bars.
"""
import json
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collect

HOST = "127.0.0.1"
PORT = 8123
COLLECT_EVERY_S = 30

STATE = {
    "snap": None,
    "last_ok": None,
    "last_fail": None,
    "fail_count": 0,
    "last_error": None,
    "started": time.time(),
}
LOCK = threading.Lock()

# distinct colors for up to 6 plugs
COLORS = ["#4f8cff", "#7ee0a3", "#ffb454", "#c08cff", "#ff7eb6", "#7ee0d6"]


def worker():
    while True:
        try:
            snap = collect.collect_data()
            with LOCK:
                STATE["snap"] = snap
                STATE["last_ok"] = time.time()
                STATE["fail_count"] = 0
                STATE["last_error"] = None
            n = len(snap["plugs"])
            print(f"[spark-dash] collect ok at {snap['generated_at']} ({n} plugs)",
                  flush=True)
        except Exception as e:
            STATE["last_fail"] = time.time()
            STATE["fail_count"] += 1
            STATE["last_error"] = str(e)[:300]
            print(f"[spark-dash] collect failed ({STATE['fail_count']}): {e}",
                  flush=True)
            traceback.print_exc()
        time.sleep(COLLECT_EVERY_S)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tapo energy plugs — live power</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:transparent;color:#ddd;font:14px/1.4 system-ui,sans-serif}
@media (prefers-color-scheme: light){body{color:#222}}
#wrap{max-width:1100px;margin:0 auto;padding:4px 4px 20px}
header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:14px}
h1{font-size:18px;margin:0}
.fresh{font-size:12px;color:#999;font-family:ui-monospace,monospace}
.fresh.stale{color:#e0b341}
.bad{background:rgba(217,83,79,.12);border:1px solid #d9534f;color:#f0a9b0;border-radius:8px;padding:8px 12px;margin-bottom:14px;font-size:13px;display:none}
@media (prefers-color-scheme: light){.bad{color:#8b2626}}
.metrics{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px}
.card{background:rgba(128,140,160,.08);border:1px solid rgba(128,140,160,.25);border-radius:12px;padding:12px 16px;min-width:200px;flex:1}
.card .label{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#999}
.card .val{font-family:ui-monospace,monospace;font-size:26px;margin-top:2px}
.card .sub{font-size:12px;color:#999;margin-top:4px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:1px}
.dot.on{background:#5cb85c}.dot.off{background:#d9534f}.dot.na{background:#888}
.plot{background:rgba(128,140,160,.08);border:1px solid rgba(128,140,160,.25);border-radius:12px;padding:14px 16px;margin-bottom:16px}
.plot .title{font-size:12px;color:#999;margin-bottom:8px}
svg{width:100%;height:auto;display:block}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:6px;font-size:12px}
.legend span{display:inline-flex;align-items:center}
.legend i{width:10px;height:10px;border-radius:2px;display:inline-block;margin-right:5px}
footer{font-size:11px;color:#777;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}
</style>
</head>
<body>
<div id="wrap">
<header>
  <h1>⚡ Tapo energy plugs — live power</h1>
  <span class="fresh" id="fresh">loading…</span>
</header>
<div class="bad" id="bad"></div>
<div class="metrics" id="metrics"></div>
<div class="plot">
  <div class="title">Live power draw — last 3 hours, 5-minute samples (W)</div>
  <div class="legend" id="lg-power"></div>
  <svg id="power" viewBox="0 0 800 240" role="img" aria-label="Power draw line chart"></svg>
</div>
<div class="plot">
  <div class="title">Energy today by hour (Wh) — hours completed so far</div>
  <svg id="energy" viewBox="0 0 800 220" role="img" aria-label="Hourly energy bar chart"></svg>
</div>
<footer><span id="gen"></span><span>tapo-mcp · auto-refresh every 20s · data every 30s</span></footer>
</div>
<script>
function el(id){return document.getElementById(id);}
function esc(s){s=String(s);var o='';for(var i=0;i<s.length;i++){var c=s.charAt(i);if(c==='&'){o+='&amp;';}else if(c==='<'){o+='&lt;';}else if(c==='>'){o+='&gt;';}else if(c==='"'){o+='&quot;';}else if(c===String.fromCharCode(39)){o+='&#39;';}else{o+=c;}}return o;}
function ts(ms){var d=new Date(ms);return String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0");}
var COLORS=__COLORS__;
function legend(id,items){
  var h=el(id);h.innerHTML="";
  items.forEach(function(it){var s=document.createElement("span");
    s.innerHTML='<i style="background:'+it.color+'"></i>'+esc(it.label);h.appendChild(s);});
}
function draw(data){
  var colors=data.plugs.map(function(_,i){return COLORS[i%COLORS.length];});
  legend("lg-power",data.plugs.map(function(p,i){return{label:p.name,color:colors[i]};}));
  var m=el("metrics");m.innerHTML="";
  data.plugs.forEach(function(p,i){
    var c=colors[i];
    var last=(p.power&&p.power.length)?p.power[p.power.length-1][1]:"n/a";
    var h=document.createElement("div");h.className="card";
    h.innerHTML='<div class="label"><span class="dot '+(p.on===true?"on":p.on===false?"off":"na")+'"></span>'+esc(p.name)+'</div>'+
      '<div class="val" style="color:'+c+'">'+last+' W</div>'+
      '<div class="sub">'+(p.on===true?"on":p.on===false?"OFF":"?")+(p.on_time?" · on "+Math.round(p.on_time/3600)+" h today":"")+'<br>'+
      'today: <b>'+(p.today_wh||0)+" Wh</b>"+(p.ip?" · "+p.ip:"")+
      (p.error?'<br><span style="color:#d9534f">'+esc(p.error)+'</span>':"")+'</div>';
    m.appendChild(h);
  });
  // power line chart
  var pw=el("power"),W=800,H=240,L=46,R=14,T=14,B=30;
  var all=[];data.plugs.forEach(function(p){(p.power||[]).forEach(function(e){all.push(e[1]);});});
  var maxW=Math.max(50,Math.ceil((all.length?Math.max.apply(null,all):10)/10)*10);
  var times=[];data.plugs.forEach(function(p){(p.power||[]).forEach(function(e){times.push(e[0]);});});
  var t0=(times.length?Math.min.apply(null,times):Date.now())-2*60000;
  var t1=(times.length?Math.max.apply(null,times):Date.now())+2*60000;
  var X=function(ms){return L+(ms-t0)/(t1-t0)*(W-L-R);},Y=function(w){return T+(1-w/maxW)*(H-T-B);};
  var s='<line x1="'+L+'" y1="'+Y(0)+'" x2="'+(W-R)+'" y2="'+Y(0)+'" stroke="currentColor" opacity=".25"/>';
  for(var g=1;g<=4;g++){var w=maxW*g/4;
    s+='<line x1="'+L+'" y1="'+Y(w)+'" x2="'+(W-R)+'" y2="'+Y(w)+'" stroke="currentColor" opacity=".1"/>'+
       '<text x="'+(L-6)+'" y="'+(Y(w)+4)+'" font-size="11" fill="currentColor" opacity=".6" text-anchor="end">'+Math.round(w)+'</text>';}
  for(var i2=0;i2<=6;i2++){var ms=t0+(t1-t0)*i2/6;
    s+='<text x="'+X(ms)+'" y="'+(H-8)+'" font-size="11" fill="currentColor" opacity=".6" text-anchor="middle">'+ts(ms)+'</text>';}
  data.plugs.forEach(function(p,pi){
    if(!p.power||!p.power.length)return;
    var d=p.power.map(function(e,k){return (k?"L":"M")+X(e[0])+" "+Y(e[1]);}).join(" ");
    s+='<path d="'+d+'" fill="none" stroke="'+colors[pi]+'" stroke-width="2.5" stroke-linejoin="round"/>';
    var lastE=p.power[p.power.length-1];
    s+='<circle cx="'+X(lastE[0])+'" cy="'+Y(lastE[1])+'" r="4" fill="'+colors[pi]+'"/>';
  });
  s+='<text x="'+(W-R)+'" y="'+(T+2)+'" font-size="11" fill="currentColor" opacity=".5" text-anchor="end">W</text>';
  pw.innerHTML=s;
  // energy bars (per plug)
  var en=el("energy"),W2=800,H2=220,L2=46,R2=14,T2=14,B2=30;
  var ev=[];data.plugs.forEach(function(p){(p.energy_pairs||[]).forEach(function(e){ev.push(e[1]||0);});});
  var maxE=Math.max(10,Math.ceil((ev.length?Math.max.apply(null,ev):10)/20)*20);
  var Y2=function(v){return T2+(1-(v||0)/maxE)*(H2-T2-B2);};
  var slot=(W2-L2-R2)/24;
  var s2='<line x1="'+L2+'" y1="'+Y2(0)+'" x2="'+(W2-R2)+'" y2="'+Y2(0)+'" stroke="currentColor" opacity=".25"/>';
  for(var g2=1;g2<=4;g2++){var e2=maxE*g2/4;
    s2+='<line x1="'+L2+'" y1="'+Y2(e2)+'" x2="'+(W2-R2)+'" y2="'+Y2(e2)+'" stroke="currentColor" opacity=".1"/>'+
        '<text x="'+(L2-6)+'" y="'+(Y2(e2)+4)+'" font-size="11" fill="currentColor" opacity=".6" text-anchor="end">'+Math.round(e2)+'</text>';}
  var np=data.plugs.length,bw=slot*0.8/np;
  data.plugs.forEach(function(p,pi){
    (p.energy_pairs||[]).forEach(function(e,hour){
      var v=e[1];if(v==null||v===0)return;
      var x=L2+hour*slot+(pi*bw+1);
      s2+='<rect x="'+x+'" y="'+Y2(v)+'" width="'+(bw-1.5)+'" height="'+(Y2(0)-Y2(v))+'" rx="2" fill="'+colors[pi]+'" opacity=".85"/>';
    });
  });
  for(var hh=0;hh<24;hh+=2){
    s2+='<text x="'+(L2+hh*slot+slot/2)+'" y="'+(H2-8)+'" font-size="11" fill="currentColor" opacity=".6" text-anchor="middle">'+String(hh).padStart(2,"0")+'</text>';
  }
  s2+='<text x="'+(W2-R2)+'" y="'+(T2+2)+'" font-size="11" fill="currentColor" opacity=".5" text-anchor="end">Wh</text>';
  en.innerHTML=s2;
  // freshness
  var f=el("fresh");
  var ageMs=Date.now()-data.generated_ms;
  var ageStr=ageMs<60000?Math.round(ageMs/1000)+" s":Math.round(ageMs/60000)+" min";
  f.textContent="data as of "+new Date(data.generated_ms).toLocaleTimeString()+" ("+ageStr+" old)";
  f.className="fresh"+(ageMs>15*60000?" stale":"");
  el("gen").textContent="generated "+new Date(data.generated_ms).toLocaleString();
}
function load(){
  fetch("data.json?t="+Date.now()).then(function(r){return r.json();}).then(function(j){
    if(j.snap)draw(j.snap);
    if(j.stale){var b=el("bad");b.style.display="block";
      b.textContent="⚠ collector failing ("+j.fail_count+"×) — showing last good data. Last error: "+(j.last_error||"?");}
    else el("bad").style.display="none";
  }).catch(function(e){
    el("bad").style.display="block";el("bad").textContent="⚠ can't reach dashboard server: "+e;
  });
}
load();
setInterval(load,20000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/data.json":
            with LOCK:
                payload = dict(STATE)
            payload.pop("started", None)
            payload["stale"] = bool(
                payload["fail_count"] > 0 and
                (payload["snap"] is None or payload["last_fail"] >
                 (payload["last_ok"] or 0)))
            if payload["snap"] is None:
                payload["error"] = "waiting for first successful collect"
            self._send(200, json.dumps(payload), "application/json")
        elif path == "/status.json":
            with LOCK:
                payload = {
                    "ok": STATE["snap"] is not None,
                    "uptime_s": round(time.time() - STATE["started"]),
                    "last_ok": STATE["last_ok"],
                    "last_fail": STATE["last_fail"],
                    "fail_count": STATE["fail_count"],
                    "last_error": STATE["last_error"],
                }
            self._send(200, json.dumps(payload), "application/json")
        else:
            page = PAGE.replace("__COLORS__", json.dumps(COLORS))
            self._send(200, page, "text/html; charset=utf-8")


def main():
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[spark-dash] serving http://{HOST}:{PORT} "
          f"(collect every {COLLECT_EVERY_S}s)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
