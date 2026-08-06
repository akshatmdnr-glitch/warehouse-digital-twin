// Real-time state feed: WebSocket with HTTP polling fallback.

export function createStateFeed({ wsPort, refreshRate, onState, onConn }) {
  let ws = null;
  let pollTimer = null;
  let stopped = false;

  function setConn(kind, ok) {
    if (onConn) onConn(kind, ok);
  }

  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.hostname}:${wsPort}/ws`);
    ws.onopen = () => { setConn('ws', true); stopPolling(); };
    ws.onmessage = (ev) => {
      try { onState(JSON.parse(ev.data).data); } catch (_) {}
    };
    ws.onclose = () => {
      setConn('ws', false);
      if (!stopped) startPolling();
      setTimeout(() => { if (!stopped && ws && ws.readyState === WebSocket.CLOSED) connectWS(); }, 3000);
    };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const st = await (await fetch('/api/state')).json();
        onState(st);
        setConn('http', true);
      } catch (_) { setConn('http', false); }
    }, Math.max(250, (refreshRate || 1) * 1000));
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function start() {
    connectWS();
    startPolling(); // warm start until WS connects
  }

  function stop() {
    stopped = true;
    stopPolling();
    if (ws) { try { ws.close(); } catch (_) {} }
  }

  return { start, stop };
}
