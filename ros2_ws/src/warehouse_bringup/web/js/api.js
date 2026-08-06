// Backend REST API helpers for the Control Center.

export async function postJSON(path, payload) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function getJSON(path) {
  const res = await fetch(path);
  return res.json();
}

export function command(payload) {
  return postJSON('/api/command', payload);
}

export function setSetting(group, key, value) {
  return postJSON('/api/settings', { group, key, value });
}

export function ackAlert(id) {
  return postJSON('/api/alerts/ack', { id });
}
