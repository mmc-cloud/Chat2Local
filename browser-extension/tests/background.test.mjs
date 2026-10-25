import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../background.js", import.meta.url), "utf8");
const token = "test-browser-token-" + "x".repeat(32);
const capability = "browser.tabs.create";
const requestId = "12345678-1234-1234-1234-123456789012";
const flush = () => new Promise((resolve) => setImmediate(resolve));

async function worker({ config = { enabled: true, port: 8766, token }, createTab } = {}) {
  const sockets = [], tabs = [], alarms = [], timeouts = new Map(), intervals = new Map();
  const events = {}, status = [];
  let sequence = 0;
  const event = (name) => ({ addListener: (callback) => { events[name] = callback; } });
  class WebSocket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
    send(raw) { this.sent.push(JSON.parse(raw)); }
    close(code = 1000) { this.closeCode = code; this.readyState = 3; this.onclose?.(); }
  }
  const chrome = {
    storage: {
      local: { get: async () => ({ connection: config }), setAccessLevel: async () => {} },
      session: { set: async (value) => { status.push(value.connectionStatus); } },
      onChanged: event("changed"),
    },
    runtime: { onInstalled: event("installed"), onStartup: event("startup"), onMessage: event("message"), openOptionsPage: async () => {} },
    alarms: { get: async () => undefined, create: async (...args) => { alarms.push(args); }, onAlarm: event("alarm") },
    action: { onClicked: event("clicked") },
    tabs: { create: async (args) => { tabs.push(args); return createTab ? createTab(args) : { id: 42, windowId: 7 }; } },
  };
  const context = vm.createContext({ chrome, WebSocket, URL, Date, TextEncoder,
    setTimeout: (fn, delay) => { const id = ++sequence; timeouts.set(id, { fn, delay }); return id; },
    clearTimeout: (id) => timeouts.delete(id),
    setInterval: (fn, delay) => { const id = ++sequence; intervals.set(id, { fn, delay }); return id; },
    clearInterval: (id) => intervals.delete(id),
  });
  context.importScripts = (file) => vm.runInContext(readFileSync(new URL("../" + file, import.meta.url), "utf8"), context);
  vm.runInContext(source, context);
  await flush();
  const open = () => { const ws = sockets.at(-1); ws.onopen(); return ws; };
  const send = (ws, message) => ws.onmessage({ data: JSON.stringify(message) });
  const auth = async (ws) => send(ws, { type: "hello_ack", protocol_version: 1, ok: true });
  return { sockets, tabs, alarms, timeouts, intervals, events, status, open, send, auth };
}

function request(overrides = {}) {
  return { type: "request", protocol_version: 1, request_id: requestId,
    capability, arguments: { url: "https://example.com" }, expires_at: Date.now() + 15000, ...overrides };
}

test("minimal manifest and private loopback connection, no page exposure", async () => {
  const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ["storage", "alarms", "tabs", "scripting"]);
  assert.deepEqual(manifest.optional_permissions, ["tabGroups"]);
  assert.deepEqual(manifest.optional_host_permissions, ["http://*/*", "https://*/*"]);
  assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1/*"]);
  assert.equal(manifest.minimum_chrome_version, "120");
  for (const key of ["content_scripts", "externally_connectable", "web_accessible_resources"]) {
    assert.equal(manifest[key], undefined);
  }
  const w = await worker();
  const ws = w.open();
  assert.equal(ws.url, "ws://127.0.0.1:8766/browser/ws");
  assert.deepEqual(ws.sent[0].capabilities, [capability, "browser.tabs.list", "browser.tabs.resolve",
    "browser.tabs.manage", "browser.windows.list", "browser.windows.manage", "browser.navigation.go",
    "browser.navigation.status", "browser.navigation.wait", "browser.groups.list", "browser.groups.manage",
    "browser.page.read", "browser.page.inspect", "browser.page.dom.inspect", "browser.page.click", "browser.page.fill",
    "browser.page.double_click", "browser.page.hover", "browser.page.focus", "browser.page.select",
    "browser.page.set_checked", "browser.page.scroll", "browser.page.press_key", "browser.page.wait_for"]);
  assert.equal(ws.sent[0].token, token);
  assert.equal(w.alarms[0][1].periodInMinutes, 0.5);
  await w.auth(ws);
  assert.equal(w.status.at(-1), "connected");
  await w.send(ws, request());
  assert.equal(w.tabs.length, 1);
  assert.equal(w.tabs[0].url, "https://example.com");
  assert.deepEqual(ws.sent.at(-1), { type: "response", protocol_version: 1, request_id: requestId,
    ok: true, result: { tab_id: 42, window_id: 7, url: "https://example.com/" } });
});

test("no browser operations before authenticated acknowledgement", async () => {
  const w = await worker();
  const ws = w.open();
  await w.send(ws, request());
  assert.equal(ws.closeCode, 1008);
  assert.equal(w.tabs.length, 0);
});

test("first request immediately after ack observes authenticated state", async () => {
  const w = await worker(), ws = w.open();
  const ack = w.auth(ws);
  const operation = w.send(ws, request());
  await Promise.all([ack, operation]);
  assert.equal(w.tabs.length, 1);
  assert.equal(ws.closeCode, undefined);
  assert.equal(ws.sent.at(-1).ok, true);
});

test("unknown operations, invalid URLs, extras and expired requests do not open tabs", async () => {
  const cases = [
    { capability: "browser.page.unknown" }, { expires_at: Date.now() - 1 },
    { arguments: {} }, { arguments: { url: 1 } }, { arguments: { url: "https://example.com", active: false } },
    ...["javascript:alert(1)", "file:///a", "chrome://settings", "https://", "https://example.com/\n",
      "https://user:pass@example.com", "https://example.com:0", "https://example.com/\\x"].map((url) => ({ arguments: { url } })),
  ];
  for (const invalid of cases) {
    const w = await worker(), ws = w.open();
    await w.auth(ws);
    await w.send(ws, request(invalid));
    assert.equal(w.tabs.length, 0);
    assert.equal(ws.sent.at(-1).ok, false);
  }
});

test("concurrent duplicate request IDs only create one tab", async () => {
  let resolve;
  const w = await worker({ createTab: () => new Promise((done) => { resolve = done; }) });
  const ws = w.open();
  await w.auth(ws);
  const input = request();
  const first = w.send(ws, input), duplicate = w.send(ws, input);
  assert.equal(w.tabs.length, 1);
  resolve({ id: 1, windowId: 2 });
  await Promise.all([first, duplicate]);
  assert.equal(w.tabs.length, 1);
  assert.equal(ws.sent.filter((m) => m.type === "response").length, 2);
});

test("Chrome rejection returns a safe error", async () => {
  const w = await worker({ createTab: () => { throw Error(token); } }), ws = w.open();
  await w.auth(ws);
  await w.send(ws, request());
  assert.equal(ws.sent.at(-1).error.code, "browser_operation_failed");
  assert.equal(JSON.stringify(ws.sent.at(-1)).includes(token), false);
});

test("heartbeat and alarms recover after close or worker restart", async () => {
  const w = await worker(), ws = w.open();
  await w.auth(ws);
  const timer = [...w.intervals.values()][0];
  assert.equal(timer.delay, 20000);
  timer.fn();
  assert.deepEqual(ws.sent.at(-1), { type: "ping" });
  ws.close();
  assert.equal(w.intervals.size, 0);
  assert.equal(w.status.at(-1), "disconnected");
  assert.ok([...w.timeouts.values()].some((timer) => timer.delay === 1000));
  w.events.alarm({ name: "chat2local-reconnect" });
  await flush();
  assert.equal(w.sockets.length, 2);
  // A new worker reads persisted configuration and connects without a popup.
  const restarted = await worker();
  assert.equal(restarted.sockets.length, 1);
});

test("disabled or invalid connection settings never create a socket", async () => {
  for (const config of [undefined, {}, { enabled: false, port: 8766, token },
    { enabled: true, port: 0, token }, { enabled: true, port: 8766, token: "bad" }]) {
    const w = await worker({ config: config ?? {} });
    assert.equal(w.sockets.length, 0);
    assert.equal(w.status.at(-1), "disabled_or_unconfigured");
  }
});
