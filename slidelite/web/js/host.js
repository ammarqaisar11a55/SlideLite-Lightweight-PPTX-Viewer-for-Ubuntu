// Bridge between the page and the Python host.
//
// Inside SlideLite the host is reached through a WebKit script message
// handler and answers by calling window.SlideLiteHost.receive().  When the UI
// is served by the localhost development server the same messages travel
// over fetch() instead.

const listeners = new Map();
const webkitHandler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.slidelite;

export const isEmbedded = Boolean(webkitHandler);

function dispatch(event, payload) {
  for (const fn of listeners.get(event) || []) {
    try { fn(payload || {}); } catch (err) { console.error(`host event ${event}:`, err); }
  }
}

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, []);
  listeners.get(event).push(fn);
}

export function send(cmd, data = {}) {
  const message = JSON.stringify({ cmd, ...data });
  if (webkitHandler) {
    webkitHandler.postMessage(message);
    return;
  }
  fetch('__bridge', { method: 'POST', body: message, headers: { 'Content-Type': 'application/json' } })
    .then((r) => r.json())
    .then((events) => events.forEach(([e, p]) => dispatch(e, p)))
    .catch(() => {});
}

window.SlideLiteHost = { receive: dispatch };

if (!webkitHandler) {
  // Development server: poll for host events.
  const poll = () => fetch('__events')
    .then((r) => r.json())
    .then((events) => events.forEach(([e, p]) => dispatch(e, p)))
    .catch(() => {})
    .finally(() => setTimeout(poll, 400));
  poll();
}
