/* Only this extension's authenticated, loopback socket can dispatch operations. */
importScripts("browser-safety.js");
importScripts("browser-navigation.js");
importScripts("browser-groups.js");
importScripts("browser-bookmarks.js");
importScripts("browser-operations.js");
const browserOperations = globalThis.Chat2LocalBrowser;
browserOperations.installAuthorizationHandler();
globalThis.Chat2LocalNavigation.install();
const RETRY_ALARM = "chat2local-reconnect";
let socket = null;
let connecting = false;
let retryTimer = null;
let heartbeatTimer = null;
let retryDelay = 1000;

function validConfig(config) {
  return config?.enabled === true && Number.isInteger(config.port)
    && config.port >= 1 && config.port <= 65535
    && typeof config.token === "string" && /^[A-Za-z0-9_-]{32,128}$/.test(config.token);
}

function setStatus(state) {
  // Never copy credentials, URLs or server-supplied diagnostics into status/logs.
  void chrome.storage.session.set({ connectionStatus: state });
}

function retry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(() => { void connect(); }, retryDelay);
  retryDelay = Math.min(retryDelay * 2, 20000);
}

async function perform(request) {
  let result = await browserOperations.execute(request);
  if (new TextEncoder().encode(JSON.stringify(result)).length > 48 * 1024) {
    result = { ok: false, error: { code: "result_too_large" } };
  }
  return { type: "response", protocol_version: 1, request_id: request.request_id, ...result };
}

async function connect() {
  if (connecting || socket !== null) return;
  connecting = true;
  try {
    const { connection: config } = await chrome.storage.local.get("connection");
    if (!validConfig(config)) { setStatus("disabled_or_unconfigured"); return; }
    setStatus("connecting");
    const ws = new WebSocket(`ws://127.0.0.1:${config.port}/browser/ws`);
    socket = ws;
    let authenticated = false;
    let lastMessage = Date.now();
    let inFlight = 0;
    // Suppress repeated request IDs within a connection, including concurrent duplicates.
    const responses = new Map();
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", protocol_version: 1,
        token: config.token, capabilities: browserOperations.capabilities }));
    };
    const authTimer = setTimeout(() => { if (!authenticated) ws.close(); }, 7000);
    ws.onmessage = async (event) => {
      if (socket !== ws) return;
      let message;
      try { message = JSON.parse(event.data); } catch { ws.close(1008, "Invalid protocol"); return; }
      if (!message || typeof message !== "object") { ws.close(1008, "Invalid protocol"); return; }
      lastMessage = Date.now();
      if (!authenticated) {
        if (message.type !== "hello_ack" || message.protocol_version !== 1 || message.ok !== true) {
          ws.close(1008, "Authentication required"); return;
        }
        authenticated = true;
        clearTimeout(authTimer);
        retryDelay = 1000;
        setStatus("connected");
        heartbeatTimer = setInterval(() => {
          if (Date.now() - lastMessage > 45000) ws.close();
          else if (ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}');
        }, 20000);
        return;
      }
      if (message.type === "pong") return;
      if (message.type !== "request" || message.protocol_version !== 1
          || typeof message.request_id !== "string"
          || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(message.request_id)
          || !Number.isFinite(message.expires_at) || message.expires_at > Date.now() + 30000) {
        ws.close(1008, "Invalid request"); return;
      }
      if (!responses.has(message.request_id)) {
        if (inFlight >= 16) { ws.close(1008, "Too many requests"); return; }
        inFlight++;
        responses.set(message.request_id, perform(message).finally(() => { inFlight--; }));
        if (responses.size > 256) responses.delete(responses.keys().next().value);
      }
      const response = await responses.get(message.request_id);
      if (socket === ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(response));
    };
    ws.onerror = () => { ws.close(); };
    ws.onclose = () => {
      clearTimeout(authTimer);
      if (socket !== ws) return;
      clearInterval(heartbeatTimer);
      socket = null;
      setStatus("disconnected");
      retry();
    };
  } catch { setStatus("connection_failed"); retry(); }
  finally { connecting = false; }
}

async function ensureAlarm() {
  if (!await chrome.alarms.get(RETRY_ALARM)) {
    await chrome.alarms.create(RETRY_ALARM, { periodInMinutes: 0.5 });
  }
}

chrome.runtime.onInstalled.addListener(() => { void ensureAlarm(); void connect(); });
chrome.runtime.onStartup.addListener(() => { void ensureAlarm(); void connect(); });
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === RETRY_ALARM) void connect(); });
chrome.action.onClicked.addListener(() => { void chrome.runtime.openOptionsPage(); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes.connection) return;
  clearTimeout(retryTimer);
  retryDelay = 1000;
  if (socket) socket.close();
  else void connect();
});
// DOM code is injected on demand; no web-page messaging, externally_connectable or remote scripts.
void chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
void ensureAlarm();
void connect();
