#!/usr/bin/env python3
"""Lightweight system monitoring service.

Serves a monitoring dashboard (CPU, memory, disk, ROS/API/DB status) that
combines local psutil samples with the warehouse backend's monitoring data.

    BACKEND_URL=http://localhost:8090 python3 scripts/system_monitor.py --serve --port 9100

Endpoints:
    GET /         monitoring HTML page (auto-refresh)
    GET /metrics  JSON snapshot
    GET /health   liveness
"""

import argparse
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

METRICS = {'ts': None, 'local': {}, 'backend': None, 'components': None}
LOCK = threading.Lock()


def collect():
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    local = {
        'ts': round(time.time(), 3),
        'cpu_percent': psutil.cpu_percent(interval=0.2),
        'memory_percent': mem.percent,
        'disk_percent': disk.percent,
        'load_avg': [round(x, 2) for x in os.getloadavg()],
        'processes': len(psutil.pids()),
    }
    backend_url = os.environ.get('BACKEND_URL', 'http://localhost:8090').rstrip('/')
    backend = None
    components = None
    try:
        with urllib.request.urlopen(f'{backend_url}/api/monitoring', timeout=5) as r:
            backend = json.loads(r.read())
    except Exception:
        backend = None
    try:
        with urllib.request.urlopen(f'{backend_url}/api/health/components', timeout=5) as r:
            components = json.loads(r.read())
    except Exception:
        components = None
    with LOCK:
        METRICS.update({'ts': time.time(), 'local': local,
                        'backend': backend, 'components': components})


_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Warehouse Monitoring</title>
<style>
 body{background:#0b0e14;color:#d7dee9;font-family:monospace;padding:20px}
 h1{font-size:18px} h2{font-size:14px;color:#38bdf8;margin-top:24px}
 table{border-collapse:collapse;width:100%;font-size:13px}
 th,td{border:1px solid #262e3d;padding:6px 10px;text-align:left}
 th{background:#161b25;color:#7f8ca3}
 .ok{color:#34d399}.bad{color:#f87171}
</style></head><body>
<h1>◈ Warehouse Monitoring</h1>
<div id="ts"></div>
<div id="panels"></div>
<script>
async function load(){
  try{
    const m = await (await fetch('/metrics')).json();
    document.getElementById('ts').textContent = 'Updated ' + new Date(m.ts*1000).toLocaleTimeString();
    const l = m.local, b = m.backend, c = m.components;
    const rows = [
      ['Local CPU', l.cpu_percent+'%'], ['Local memory', l.memory_percent+'%'],
      ['Disk', l.disk_percent+'%'], ['Load avg', l.load_avg.join(', ')],
      ['Processes', l.processes],
    ];
    if(b){ rows.push(['API CPU', b.live.cpu_percent+'%'], ['API RSS', b.live.rss_mb+' MB'],
        ['API requests', b.live.requests_total], ['WS clients', b.live.ws_clients]); }
    let html = '<h2>System</h2><table><tr>'+rows.map(r=>'<th>'+r[0]+'</th>').join('')+'</tr><tr>'+
        rows.map(r=>'<td>'+r[1]+'</td>').join('')+'</tr></table>';
    if(c){
      html += '<h2>Components</h2><table><tr><th>component</th><th>status</th><th>detail</th></tr>';
      const cb = {db:c.db, ros_bridge:c.ros_bridge, fleet:c.fleet, dashboard:c.dashboard,
                  analytics:c.analytics, api:c.api};
      for(const [k,v] of Object.entries(cb)){
        const ok = v && (v.ok !== false);
        html += '<tr><td>'+k+'</td><td class="'+(ok?'ok':'bad')+'">'+(v.status||(ok?'ok':'down'))+
          '</td><td>'+(v.latest?'robots='+v.latest.robot_count:v.url||'')+'</td></tr>';
      }
      html += '</table>';
    }
    document.getElementById('panels').innerHTML = html;
  }catch(e){}
}
setInterval(load, 2000);
load();
</script></body></html>
"""


def serve(port):
    def run():
        while True:
            collect()
            time.sleep(3)

    threading.Thread(target=run, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/metrics':
                self._json()
            elif self.path == '/health':
                self._json({'status': 'ok'})
            else:
                self._send(_PAGE, 'text/html; charset=utf-8')

        def _json(self):
            with LOCK:
                body = json.dumps(METRICS)
            self._send(body, 'application/json')

        def _send(self, body, ctype):
            data = body.encode()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    print(f'monitoring on http://localhost:{port}')
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--serve', action='store_true')
    ap.add_argument('--port', type=int, default=9100)
    args = ap.parse_args()
    if args.serve:
        serve(args.port)
    else:
        collect()
        print(json.dumps(METRICS, indent=2))


if __name__ == '__main__':
    main()
