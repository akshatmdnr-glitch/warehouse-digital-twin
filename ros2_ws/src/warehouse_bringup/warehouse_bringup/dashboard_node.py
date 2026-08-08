"""Real-time warehouse web dashboard (read-only observer + HTTP server).

Subscribes to existing global topics and serves an HTML page plus a JSON
state endpoint. It never publishes to the ROS graph and never modifies robot
or fleet behavior.

Subscribes: /fleet_status, /fleet_monitor, /analytics, /reservation_status,
            /map, /robot1/map, /robot2/map (String/JSON or OccupancyGrid)
Serves:     GET /            -> HTML dashboard (auto-refreshes every second)
            GET /api/state   -> JSON snapshot of the warehouse
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from std_msgs.msg import String

# Reduce the map payload to at most this many cells per axis.
_MAP_MAX = 128


class DashboardNode(Node):
    def __init__(self):
        super().__init__("dashboard")
        self.declare_parameter("port", 8080)
        self.declare_parameter("refresh_interval", 1.0)

        self._port = int(self.get_parameter("port").value)
        self._refresh = float(self.get_parameter("refresh_interval").value)

        # Latest snapshots of the subscribed topics (state is not duplicated —
        # the dashboard only aggregates existing publications).
        self._fleet = {"robots": [], "robot_count": 0}
        self._fleet_monitor = {}
        self._analytics = {}
        self._reservations = {
            "reservations": [],
            "pending_dispatches": [],
            "retry_tasks": [],
        }
        self._map = None  # {'width','height','resolution','origin','data':[...]}

        self._fleet_sub = self.create_subscription(
            String, "/fleet_status", self._on_fleet, 10
        )
        self._monitor_sub = self.create_subscription(
            String, "/fleet_monitor", self._on_monitor, 10
        )
        self._analytics_sub = self.create_subscription(
            String, "/analytics", self._on_analytics, 10
        )
        self._res_sub = self.create_subscription(
            String, "/reservation_status", self._on_reservations, 10
        )
        self._map_sub = self.create_subscription(
            OccupancyGrid, "/map", self._on_map, 10
        )
        self._map1_sub = self.create_subscription(
            OccupancyGrid, "/robot1/map", self._on_map, 10
        )
        self._map2_sub = self.create_subscription(
            OccupancyGrid, "/robot2/map", self._on_map, 10
        )

        # HTTP server (non-blocking).
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self._port), self._make_handler())
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self.get_logger().info(
            f"Dashboard ready on http://localhost:{self._port} "
            f"(refresh={self._refresh}s)"
        )

    # ── Topic callbacks ────────────────────────────────────────

    def _on_fleet(self, msg):
        try:
            self._fleet = json.loads(msg.data)
        except (ValueError, TypeError):
            pass

    def _on_monitor(self, msg):
        try:
            self._fleet_monitor = json.loads(msg.data)
        except (ValueError, TypeError):
            pass

    def _on_analytics(self, msg):
        try:
            self._analytics = json.loads(msg.data)
        except (ValueError, TypeError):
            pass

    def _on_reservations(self, msg):
        try:
            self._reservations = json.loads(msg.data)
        except (ValueError, TypeError):
            pass

    def _on_map(self, msg):
        # Downsample so the /api/state payload stays small.
        width, height = msg.info.width, msg.info.height
        step = max(1, (width * height) // (_MAP_MAX * _MAP_MAX))
        # msg.data is an array.array('b') — convert to a plain list so the
        # JSON snapshot serializes cleanly.
        data = list(msg.data[::step])
        self._map = {
            "width": width,
            "height": height,
            "resolution": msg.info.resolution,
            "origin": [msg.info.origin.position.x, msg.info.origin.position.y],
            "step": step,
            "data": data,
        }

    # ── State aggregation ──────────────────────────────────────

    def _snapshot(self):
        robots_raw = self._fleet.get("robots", [])
        robots = []
        active = idle = charging = offline = 0
        for r in robots_raw:
            entry = {
                "id": r.get("robot_id"),
                "namespace": r.get("namespace", ""),
                "status": r.get("status"),
                "x": r.get("x"),
                "y": r.get("y"),
                "yaw": r.get("yaw"),
                "battery": r.get("battery"),
                "charging": bool(r.get("charging", False)),
                "exec_state": r.get("exec_state", "") or "UNKNOWN",
                "moving": bool(r.get("moving", False)),
                "current_task": r.get("current_task", ""),
            }
            # Merge per-robot analytics if available.
            for a in self._analytics.get("robots", []):
                if a.get("robot_id") == entry["id"]:
                    entry.update(
                        {
                            "active_time": a.get("active_time"),
                            "idle_time": a.get("idle_time"),
                            "charging_time": a.get("charging_time"),
                            "battery_usage": a.get("battery_usage"),
                            "distance": a.get("distance_traveled"),
                            "completed_tasks": a.get("completed_tasks"),
                        }
                    )
                    break
            robots.append(entry)
            if entry["status"] == "OFFLINE":
                offline += 1
            elif entry["charging"]:
                charging += 1
            elif entry["current_task"]:
                active += 1
            else:
                idle += 1

        # Utilization = active time / total observed time (fleet-wide).
        a_robots = self._analytics.get("robots", [])
        total_time = sum(
            a.get("active_time", 0.0)
            + a.get("idle_time", 0.0)
            + a.get("charging_time", 0.0)
            for a in a_robots
        )
        active_time = sum(a.get("active_time", 0.0) for a in a_robots)
        utilization = round(active_time / total_time, 3) if total_time > 0 else None

        fleet_mon = self._fleet_monitor.get("tasks", {})
        an_fleet = self._analytics.get("fleet", {})
        return {
            "timestamp": self.get_clock().now().nanoseconds / 1e9,
            "refresh_interval": self._refresh,
            "fleet": {
                "total": len(robots),
                "active": active,
                "idle": idle,
                "charging": charging,
                "offline": offline,
                "completed": fleet_mon.get(
                    "completed", an_fleet.get("total_completed_tasks", 0)
                ),
                "queue_length": fleet_mon.get("queued", 0),
            },
            "analytics": {
                "avg_task_duration": an_fleet.get("avg_task_duration"),
                "avg_queue_wait": an_fleet.get("avg_queue_wait"),
                "avg_reservation_wait": an_fleet.get("avg_reservation_wait"),
                "utilization": utilization,
                "total_battery_usage": round(
                    sum(a.get("battery_usage", 0.0) for a in a_robots), 1
                ),
            },
            "robots": robots,
            "reservations": [
                {
                    "robot_id": r.get("robot_id"),
                    "task_id": r.get("task_id"),
                    "segment_index": r.get("segment_index"),
                    "segments_reserved": r.get("segments_reserved"),
                    "total_segments": r.get("total_segments"),
                    "head_on": r.get("head_on"),
                }
                for r in self._reservations.get("reservations", [])
            ],
            "queue": [
                {"robot_id": p.get("robot_id"), "task_id": p.get("task_id")}
                for p in self._reservations.get("pending_dispatches", [])
            ]
            + [
                {"robot_id": "—", "task_id": t}
                for t in self._reservations.get("retry_tasks", [])
            ],
            "map": self._map,
        }

    # ── HTTP ───────────────────────────────────────────────────

    def _make_handler(self):
        node = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/api/state"):
                    self._json(json.dumps(node._snapshot()))
                else:
                    self._html(_PAGE)

            def _json(self, body):
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _html(self, body):
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt, *args):
                pass  # quiet

        return Handler


_PAGE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Warehouse Fleet Dashboard</title>
<style>
 body{font-family:monospace;background:#101418;color:#d8e0e8;margin:16px}
 h1{font-size:18px;margin:0 0 4px 0}
 .row{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}
 .card{background:#1a2026;border:1px solid #2c3640;border-radius:6px;padding:10px;min-width:110px}
 .card .v{font-size:22px;font-weight:bold}
 .card .l{font-size:11px;color:#8fa3b5}
 table{border-collapse:collapse;width:100%;font-size:12px}
 th,td{border:1px solid #2c3640;padding:4px 8px;text-align:left}
 th{background:#1a2026}
 .ok{color:#4caf50}.bad{color:#f44336}.warn{color:#ff9800}
 canvas{border:1px solid #2c3640;background:#0c0f12}
 .col{display:inline-block;vertical-align:top}
</style></head><body>
<h1>Warehouse Fleet Dashboard</h1>
<div id="ts" style="font-size:12px;color:#8fa3b5"></div>
<div class="row" id="cards"></div>
<div class="row" id="an"></div>
<div class="row">
 <div class="col"><canvas id="map" width="520" height="520"></canvas></div>
 <div class="col" style="margin-left:12px"><table id="robots"></table></div>
</div>
<div class="row">
 <div class="col" style="width:49%"><h2 style="font-size:14px">Reservations</h2><table id="res"></table></div>
 <div class="col" style="width:49%"><h2 style="font-size:14px">Task Queue</h2><table id="queue"></table></div>
</div>
<script>
let state = null;
const $ = id => document.getElementById(id);
function el(t, txt){const e=document.createElement(t);if(txt!==undefined)e.textContent=txt;return e;}
async function refresh(){
  try{
    const r = await fetch('/api/state');
    state = await r.json();
  }catch(e){ return; }
  $('ts').textContent = 'Updated ' + new Date(state.timestamp*1000).toLocaleTimeString() +
      '  ·  refresh ' + state.refresh_interval + 's';
  const f = state.fleet, a = state.analytics;
  $('cards').innerHTML = '';
  const cards = [['Active',f.active,'ok'],['Idle',f.idle,''],['Charging',f.charging,'warn'],
    ['Offline',f.offline,'bad'],['Completed',f.completed,'ok'],['Queue',f.queue_length,'']];
  for(const [l,v,c] of cards){
    const d=document.createElement('div'); d.className='card';
    d.innerHTML = '<div class="v '+c+'">'+v+'</div><div class="l">'+l+'</div>';
    $('cards').appendChild(d);
  }
  $('an').innerHTML = '';
  const an = [['Task duration', a.avg_task_duration],['Queue wait', a.avg_queue_wait],
    ['Reservation wait', a.avg_reservation_wait],['Utilization', a.utilization],
    ['Battery usage', a.total_battery_usage]];
  for(const [l,v] of an){
    const d=document.createElement('div'); d.className='card';
    d.innerHTML = '<div class="v">'+(v===null?'—':v)+'</div><div class="l">'+l+'</div>';
    $('an').appendChild(d);
  }
  // Robots
  const rt = $('robots'); rt.innerHTML='';
  rt.appendChild(el('tr')).append(
    ...['Robot','NS','Status','Exec','Task','Battery','X','Y','Yaw'].map(h=>{const th=el('th');th.textContent=h;return th;}));
  for(const r of state.robots){
    const tr=el('tr');
    [r.id, r.namespace, r.status, r.exec_state,
     r.current_task||'—',
     (r.battery===null?'—':r.battery+'%'), r.x===null?'—':r.x.toFixed(2),
     r.y===null?'—':r.y.toFixed(2), r.yaw===null?'—':(r.yaw*180/Math.PI).toFixed(0)+'°']
      .forEach(t=>{const td=el('td');td.textContent=t;tr.appendChild(td);});
    rt.appendChild(tr);
  }
  // Reservations
  const rs=$('res'); rs.innerHTML='';
  rs.appendChild(el('tr')).append(
    ...['Robot','Task','Seg','Reserved','Head-on'].map(h=>{const th=el('th');th.textContent=h;return th;}));
  for(const r of state.reservations){
    const tr=el('tr');
    [r.robot_id, r.task_id, r.segment_index+'/'+r.total_segments,
     JSON.stringify(r.segments_reserved), r.head_on?'yes':'no'].forEach(t=>{const td=el('td');td.textContent=t;tr.appendChild(td);});
    rs.appendChild(tr);
  }
  // Queue
  const qt=$('queue'); qt.innerHTML='';
  qt.appendChild(el('tr')).append(
    ...['Robot','Task'].map(h=>{const th=el('th');th.textContent=h;return th;}));
  for(const q of state.queue){
    const tr=el('tr'); [q.robot_id, q.task_id].forEach(t=>{const td=el('td');td.textContent=t;tr.appendChild(td);});
    qt.appendChild(tr);
  }
  drawMap();
}
function drawMap(){
  const cv=$('map'), cx=cv.getContext('2d');
  cx.clearRect(0,0,cv.width,cv.height);
  const m=state.map;
  let w=20,h=20,res=1,ox=-10,oy=-10,data=[];
  if(m){w=m.width;h=m.height;res=m.resolution;ox=m.origin[0];oy=m.origin[1];data=m.data;}
  const s=Math.min(cv.width/w, cv.height/h);
  const y0=cv.height-h*s;
  for(let i=0;i<data.length;i++){
    const gx=i%(Math.ceil(w/m.step||1))*m.step, gy=Math.floor(i/(w/m.step||1))*m.step;
    const v=data[i];
    if(v===100){cx.fillStyle='#3a4654';}
    else if(v>50){cx.fillStyle='#2a323c';}
    else{cx.fillStyle='#0c0f12';}
    cx.fillRect(gx*s, y0+gy*s, s, s);
  }
  // Robots
  for(const r of state.robots){
    if(r.x===null||r.y===null) continue;
    const px=(r.x-ox)/res*s, py=(r.y-oy)/res*s;
    cx.fillStyle = r.charging?'#ff9800':(r.status==='OFFLINE'?'#555':(r.moving?'#4caf50':(r.current_task?'#8bc34a':'#2196f3')));
    cx.beginPath(); cx.arc(px, y0+py, 8, 0, 2*Math.PI); cx.fill();
    cx.strokeStyle='#fff';
    const ang=r.yaw||0;
    cx.beginPath(); cx.moveTo(px,y0+py);
    cx.lineTo(px+14*Math.cos(ang), y0+py+14*Math.sin(ang)); cx.stroke();
    cx.fillStyle='#fff'; cx.font='12px monospace';
    cx.fillText(r.id, px+10, y0+py-6);
  }
}
setInterval(refresh, 1000);
refresh();
</script></body></html>
"""


def main():
    rclpy.init()
    node = DashboardNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
