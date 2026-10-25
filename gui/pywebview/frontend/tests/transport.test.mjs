import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import { JSDOM } from "jsdom";

const require = createRequire(import.meta.url);
const { URL, AbortSignal, setTimeout, clearTimeout } = globalThis;
const ts = require("typescript");
const source = readFileSync(new URL("../src/lib/bridge.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;

function fixture(web = true) {
  const dom = new JSDOM(web ? '<meta name="chat2local-transport" content="http">' : "", { url: "http://127.0.0.1:8767" });
  const calls = [], copied = [];
  const navigator = { clipboard: { writeText: async (value) => copied.push(value) } };
  const fetch = async (path, options = {}) => {
    calls.push([path, options]);
    const data = path === "/api/session" ? { ok: true, app: "Chat2Local", backend: "http", platform: "linux", version: "test", csrf: "SESSION-CSRF" }
      : { ok: true, result: path === "/api/copy_token" ? { token: "PRIVATE-COPY-TOKEN" } : { valid: true } };
    return { ok: true, json: async () => data };
  };
  const module = { exports: {} };
  vm.runInNewContext(code, { require, module, exports: module.exports, document: dom.window.document,
    window: dom.window, navigator, fetch, AbortSignal, setTimeout, clearTimeout });
  return { ...module.exports, dom, calls, copied, navigator };
}

test("HTTP build uses same-origin JSON calls with a session CSRF header", async () => {
  const f = fixture();
  try {
    const session = await f.connectBridge();
    assert.equal(session.backend, "http");
    assert.equal(session.platform, "linux");
    await f.coreApi.validateConfig({ read: { max_lines: 4 } });
    await f.coreApi.saveConfig({ read: { max_lines: 4 } });
    assert.equal(f.calls.filter(([path]) => path === "/api/session").length, 1);
    for (const [path, options] of f.calls.slice(1)) {
      assert.ok(path.startsWith("/api/"));
      assert.equal(options.method, "POST");
      assert.equal(options.mode, "same-origin");
      assert.equal(options.cache, "no-store");
      assert.equal(options.headers["X-Chat2Local-CSRF"], "SESSION-CSRF");
      assert.deepEqual(JSON.parse(options.body), { candidate: { read: { max_lines: 4 } } });
    }
  } finally { f.dom.window.close(); }
});

test("HTTP clipboard uses explicit protected copy action and reports clipboard errors safely", async () => {
  const f = fixture();
  try {
    assert.equal((await f.coreApi.copyToken("browser", "/private/browser.token")).copied, true);
    assert.deepEqual(f.copied, ["PRIVATE-COPY-TOKEN"]);
    f.navigator.clipboard.writeText = async () => { throw Error("PRIVATE-COPY-TOKEN"); };
    await assert.rejects(f.coreApi.copyToken("browser", null), /Clipboard unavailable; try copying again/);
    assert.equal(JSON.stringify(f.calls).includes("PRIVATE-COPY-TOKEN"), false);
    await assert.rejects(f.coreApi.restart(), /Desktop operation unavailable/);
    assert.equal(f.calls.some(([path]) => path === "/api/restart_core"), false);
  } finally { f.dom.window.close(); }
});

test("pywebview build waits for native readiness and never falls back to HTTP", async () => {
  const f = fixture(false);
  try {
    const pending = f.connectBridge();
    f.dom.window.pywebview = { api: {
      ping: async () => ({ ok: true, app: "Chat2Local", version: "test", backend: "pywebview" }),
      read_config: async () => ({ ok: true, result: { persisted: {} } }),
    } };
    f.dom.window.dispatchEvent(new f.dom.window.Event("pywebviewready"));
    assert.equal((await pending).backend, "pywebview");
    assert.deepEqual((await f.coreApi.readConfig()).persisted, {});
    assert.equal(f.calls.length, 0);
  } finally { f.dom.window.close(); }
});

test("HTTP startup switch uses only the fixed boolean management API", async () => {
  const f = fixture();
  try {
    await f.autostartApi.status();
    await f.autostartApi.setEnabled(false);
    const [path, options] = f.calls.at(-1);
    assert.equal(path, "/api/set_autostart");
    assert.deepEqual(JSON.parse(options.body), { enabled: false });
  } finally { f.dom.window.close(); }
});

test("Handoff browsing transports send identities through the two read-only operations", async () => {
  for (const web of [true, false]) {
    const f = fixture(web);
    const calls = [];
    try {
      if (!web) f.dom.window.pywebview = { api: {
        ping: async () => ({ ok: true, app: "Chat2Local", version: "test", backend: "pywebview" }),
        list_local_handoffs: async () => { calls.push(["list"]); return { ok: true, result: {} }; },
        get_local_handoff: async (id) => { calls.push(["get", id]); return { ok: true, result: {} }; },
      } };
      await f.coreApi.localHandoffs();
      await f.coreApi.localHandoff("6162:design");
      if (web) {
        const request = f.calls.at(-1);
        assert.equal(request[0], "/api/get_local_handoff");
        assert.deepEqual(JSON.parse(request[1].body), { handoff_id: "6162:design" });
        assert.equal(request[1].headers["X-Chat2Local-CSRF"], "SESSION-CSRF");
        assert.equal(f.calls[1][0], "/api/list_local_handoffs");
      } else {
        assert.deepEqual(calls, [["list"], ["get", "6162:design"]]);
        assert.equal(f.calls.length, 0);
      }
    } finally { f.dom.window.close(); }
  }
});
