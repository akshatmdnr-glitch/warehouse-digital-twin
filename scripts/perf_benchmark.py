#!/usr/bin/env python3
"""Performance benchmark for the warehouse platform.

Measures:
  - API latency  (GET /api/health, /api/robots, /api/monitoring)
  - Ingest scale (batches with 10 / 50 / 100 / 250 robots)
  - Dashboard latency (Control Center /api/state, if running)

Usage:
    python3 scripts/perf_benchmark.py
    python3 scripts/perf_benchmark.py --base http://localhost:8090 --cc http://localhost:8081
"""

import argparse
import json
import statistics
import time
import urllib.request

BASE = 'http://localhost:8090'
CC = 'http://localhost:8081'


def _get(url, token=None, timeout=30):
    req = urllib.request.Request(url)
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _post(url, payload, token=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def login():
    return _post(f'{BASE}/api/auth/login', {'username': 'admin', 'password': 'admin'})['token']


def bench_api(token, path, n=100):
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        _get(f'{BASE}{path}', token)
        lat.append((time.perf_counter() - t0) * 1000)
    return {'endpoint': path, 'n': n,
            'avg_ms': round(statistics.mean(lat), 2),
            'p50_ms': round(statistics.median(lat), 2),
            'p99_ms': round(sorted(lat)[int(n * 0.99) - 1], 2)}


def bench_ingest(token, robot_counts=(10, 50, 100, 250), batches=20):
    results = []
    for count in robot_counts:
        robots = [
            {'robot_id': f'r{i}', 'status': 'ONLINE', 'x': i * 0.5, 'y': 0.0,
             'battery': 80.0, 'charging': False, 'current_task': '',
             'namespace': f'r{i}'}
            for i in range(count)
        ]
        t0 = time.perf_counter()
        for _ in range(batches):
            _post(f'{BASE}/api/ingest',
                  {'ts': time.time(), 'robots': robots}, token)
        dt = time.perf_counter() - t0
        results.append({
            'robots': count, 'batches': batches,
            'total_s': round(dt, 3),
            'robots_per_s': round((count * batches) / dt, 1),
        })
    return results


def bench_cc():
    try:
        n = 30
        lat = []
        for _ in range(n):
            t0 = time.perf_counter()
            _get(f'{CC}/api/state')
            lat.append((time.perf_counter() - t0) * 1000)
        return {'endpoint': '/api/state', 'n': n,
                'avg_ms': round(statistics.mean(lat), 2),
                'p99_ms': round(sorted(lat)[int(n * 0.99) - 1], 2)}
    except Exception as e:
        return {'error': str(e)}


def main():
    global BASE, CC
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--cc', default=CC)
    args = ap.parse_args()
    BASE, CC = args.base, args.cc

    print(f'== Performance benchmark ({BASE}) ==')
    token = login()
    results = []
    for path in ('/api/health', '/api/robots', '/api/tasks', '/api/events',
                 '/api/fleet', '/api/analytics', '/api/monitoring'):
        r = bench_api(token, path)
        results.append(r)
        print(f"  API {r['endpoint']:<18} avg={r['avg_ms']}ms p50={r['p50_ms']}ms p99={r['p99_ms']}ms")

    print('\n== Ingest scale ==')
    scale = bench_ingest(token)
    for s in scale:
        print(f"  {s['robots']:>4} robots: {s['batches']} batches in {s['total_s']}s -> {s['robots_per_s']} robots/s")

    print('\n== Dashboard latency (Control Center) ==')
    cc = bench_cc()
    print(' ', cc)

    print('\nSummary saved to /tmp/perf_report.json')
    json.dump({'api': results, 'ingest': scale, 'dashboard': cc},
              open('/tmp/perf_report.json', 'w'), indent=2)


if __name__ == '__main__':
    main()
