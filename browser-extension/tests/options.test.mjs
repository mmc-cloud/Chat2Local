import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { JSDOM } from "jsdom";

const source = (name) => readFileSync(new URL(`../${name}`, import.meta.url), "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));
const credential = "private_test_credential_0123456789abcdef";
const allOrigins = ["http://*/*", "https://*/*"];

async function fixture(t, { storage = {}, session = {}, grants = [], fail = {}, themePreference, dark = false } = {}) {
  const dom = new JSDOM(source("options.html"), { url: "https://extension.test/", runScripts: "outside-only" });
  t.after(() => dom.window.close());
  const { window } = dom;
  const systemTheme = { matches: dark, listeners: [], addEventListener(event, fn) { this.listeners.push(fn); } };
  window.matchMedia = () => systemTheme;
  if (themePreference !== undefined) window.localStorage.setItem("optionsTheme", themePreference);
  if (fail.themeGet) window.Storage.prototype.getItem = () => { throw Error("Storage unavailable"); };
  if (fail.themeSet) window.Storage.prototype.setItem = () => { throw Error("Storage unavailable"); };
  const listeners = { changed: [], added: [], removed: [] };
  const granted = new Set(["http://127.0.0.1/*", ...grants]);
  const requests = [], removals = [], writes = [];
  const state = { storage, session, granted, fail, requests, removals, writes, inGesture: false, denied: false };
  const emit = (event, ...args) => listeners[event].forEach((fn) => fn(...args));
  const check = (name) => { if (fail[name]) throw Error(`API error with ${credential}`); };
  const contains = (value) => (value.origins || value.permissions).every((entry) => granted.has(entry)
    || (value.origins && granted.has(`${entry.split(":")[0]}://*/*`)));
  window.chrome = { runtime: { id: "abcdefghijklmnopabcdefghijklmnop" }, storage: {
    local: {
      get: async () => { check("get"); return structuredClone(storage); },
      set: async (value) => {
        check("set");
        const changes = Object.fromEntries(Object.entries(value).map(([key, newValue]) => [key, { oldValue: storage[key], newValue }]));
        Object.assign(storage, structuredClone(value)); writes.push(structuredClone(value));
        emit("changed", changes, "local");
      },
    },
    session: { get: async () => { check("session"); return structuredClone(session); } },
    onChanged: { addListener(fn) { listeners.changed.push(fn); } },
  }, permissions: {
    contains: async (value) => { check("contains"); return contains(value); },
    request: async (value) => {
      assert.equal(state.inGesture, true, "permission request must happen before the user gesture ends");
      requests.push(structuredClone(value)); check("request");
      if (state.denied) return false;
      for (const entry of value.origins || value.permissions) granted.add(entry);
      emit("added", value); return true;
    },
    remove: async (value) => {
      removals.push(structuredClone(value));
      // The application gate must already be disabled, even if the API fails.
      if (value.permissions?.includes("tabGroups")) assert.equal(storage.groupAccess, false);
      if (value.permissions?.includes("bookmarks")) assert.equal(storage.bookmarkAccess, false);
      if (value.origins?.includes("http://*/*")) assert.equal(storage.allWebsites, false);
      if (value.origins?.length === 1) assert.ok(!storage.pageOrigins.includes(value.origins[0]));
      check("remove");
      if (fail.removeFalse) return false;
      for (const entry of value.origins || value.permissions) granted.delete(entry);
      emit("removed", value); return true;
    },
    onAdded: { addListener(fn) { listeners.added.push(fn); } },
    onRemoved: { addListener(fn) { listeners.removed.push(fn); } },
  } };
  const $ = (selector) => window.document.querySelector(selector);
  const text = (selector) => $(selector).textContent;
  function gesture(selector, submit = false) {
    state.inGesture = true;
    try {
      if (submit) $(selector).dispatchEvent(new window.Event("submit", { cancelable: true }));
      else $(selector).click();
    } finally { state.inGesture = false; }
    return flush();
  }
  function status(value) {
    session.connectionStatus = value;
    emit("changed", { connectionStatus: { newValue: value } }, "session");
  }
  async function externalPermission(entry, add = false) {
    if (add) granted.add(entry); else granted.delete(entry);
    emit(add ? "added" : "removed", {});
    await flush();
  }
  window.eval(source("browser-operations.js"));
  window.eval(source("options.js"));
  await flush();
  return { ...state, $, text, gesture, status, externalPermission, emit, window, state, systemTheme };
}

test("theme defaults to the system and follows live system changes", async (t) => {
  const f = await fixture(t, { dark: true });
  assert.equal(f.$("#theme").value, "system");
  assert.equal(f.window.document.documentElement.dataset.theme, "dark");
  f.systemTheme.matches = false;
  f.systemTheme.listeners.forEach((fn) => fn());
  assert.equal(f.window.document.documentElement.dataset.theme, "light");
  assert.equal(f.writes.length, 0);
});

test("explicit themes apply immediately, survive reopening, and override the system", async (t) => {
  const f = await fixture(t);
  for (const choice of ["dark", "light", "system"]) {
    f.$("#theme").value = choice;
    f.$("#theme").dispatchEvent(new f.window.Event("change"));
    assert.equal(f.window.document.documentElement.dataset.theme, choice === "system" ? "light" : choice);
    assert.equal(f.window.localStorage.getItem("optionsTheme"), choice);
    assert.match(f.text("#theme-feedback"), /偏好已保存/);
    const reopened = await fixture(t, { themePreference: choice, dark: true });
    assert.equal(reopened.$("#theme").value, choice);
    assert.equal(reopened.window.document.documentElement.dataset.theme, choice === "system" ? "dark" : choice);
  }
  f.$("#theme").value = "light";
  f.$("#theme").dispatchEvent(new f.window.Event("change"));
  f.systemTheme.matches = true;
  f.systemTheme.listeners.forEach((fn) => fn());
  assert.equal(f.window.document.documentElement.dataset.theme, "light");
  assert.equal(f.writes.length, 0);
  assert.equal(f.requests.length, 0);
});

test("unknown or inaccessible theme preferences fall back safely with save failure feedback", async (t) => {
  const invalid = await fixture(t, { themePreference: "invalid", dark: true });
  assert.equal(invalid.$("#theme").value, "system");
  assert.equal(invalid.window.document.documentElement.dataset.theme, "dark");
  const f = await fixture(t, { fail: { themeGet: true, themeSet: true } });
  assert.match(f.text("#theme-feedback"), /无法读取主题偏好/);
  f.$("#theme").value = "dark";
  f.$("#theme").dispatchEvent(new f.window.Event("change"));
  assert.equal(f.window.document.documentElement.dataset.theme, "dark");
  assert.match(f.text("#theme-feedback"), /无法保存偏好/);
  assert.equal(f.$("#connection-fields").disabled, false);
});

test("first install has useful defaults and never grants permission on load", async (t) => {
  const f = await fixture(t);
  assert.equal(f.$("#port").value, "8766");
  assert.equal(f.$("#enabled").checked, false);
  assert.equal(f.$("#token").type, "password");
  assert.equal(f.$("#token").value, "");
  assert.equal(f.$("#token").required, true);
  assert.match(f.text("#status"), /未连接.*尚未配置/);
  assert.match(f.text("#token-state"), /未配置/);
  assert.equal(f.$("#websites-empty").hidden, false);
  assert.equal(f.requests.length, 0);
  assert.equal(f.writes.length, 0);
  await f.gesture("#settings", true);
  assert.match(f.text("#saved"), /凭据必须/);
  assert.equal(f.writes.length, 0);
});

test("saved credentials never enter the DOM and blank edits preserve the latest stored token", async (t) => {
  const f = await fixture(t, { storage: { connection: { enabled: true, port: 8766, token: credential }, unrelated: "kept" } });
  assert.equal(f.$("#token").value, "");
  assert.equal(f.$("#token").required, false);
  assert.match(f.text("#token-state"), /已配置/);
  assert.equal(f.window.document.documentElement.outerHTML.includes(credential), false);
  f.storage.connection.token = `${credential}_new`;
  f.$("#port").value = "9876";
  f.$("#enabled").checked = false;
  await f.gesture("#settings", true);
  assert.deepEqual(f.storage.connection, { enabled: false, port: 9876, token: `${credential}_new` });
  assert.equal(f.storage.unrelated, "kept");
  assert.match(f.text("#saved"), /已保存/);
  assert.equal(f.$("#token").value, "");
  f.$("#token").value = credential;
  await f.gesture("#settings", true);
  assert.equal(f.storage.connection.token, credential);
  assert.equal(f.$("#token").value, "");
});

test("initial connection can be configured, and invalid edits or storage failures are reported safely", async (t) => {
  const f = await fixture(t);
  f.$("#enabled").checked = true;
  f.$("#token").value = credential;
  await f.gesture("#settings", true);
  assert.deepEqual(f.storage.connection, { enabled: true, port: 8766, token: credential });
  for (const value of ["0", "65536", "1.5", ""]) {
    f.$("#port").value = value;
    await f.gesture("#settings", true);
    assert.match(f.text("#saved"), /端口必须/);
  }
  f.$("#port").value = "8766";
  f.$("#token").value = "invalid";
  await f.gesture("#settings", true);
  assert.match(f.text("#saved"), /凭据必须/);
  f.$("#token").value = credential;
  f.fail.set = true;
  await f.gesture("#settings", true);
  assert.match(f.text("#saved"), /保存失败/);
  assert.equal(f.text("#saved").includes(credential), false);
  assert.equal(f.$("#connection-fields").disabled, false);
});

test("connection states are localized and distinct; unknown diagnostics are not rendered", async (t) => {
  const f = await fixture(t, { storage: { connection: { enabled: true, port: 8766, token: credential } } });
  for (const [state, expected, tone] of [["connected", /已连接/, "success"], ["connecting", /连接中/, "info"],
    ["disconnected", /未连接.*等待重连/, "neutral"], ["connection_failed", /连接异常/, "error"]]) {
    f.status(state);
    assert.match(f.text("#status"), expected);
    assert.equal(f.$("#status").dataset.tone, tone);
  }
  for (const unknown of [credential, "constructor", "__proto__"]) {
    f.status(unknown);
    assert.equal(f.text("#status"), "未连接");
  }
  f.status("disabled_or_unconfigured");
  assert.match(f.text("#status"), /请检查连接配置/);
  f.status(undefined);
  assert.equal(f.text("#status"), "未连接");
  f.emit("changed", { connection: { newValue: undefined } }, "local");
  assert.match(f.text("#token-state"), /未配置/);
  assert.equal(f.$("#token").required, true);
});

test("read failures keep the connection form safe and show explicit feedback", async (t) => {
  const f = await fixture(t, { fail: { get: true, session: true } });
  assert.equal(f.$("#connection-fields").disabled, true);
  assert.match(f.text("#saved"), /无法读取连接设置/);
  assert.match(f.text("#status"), /读取失败/);
  assert.match(f.text("#websites-error"), /无法读取/);
  assert.match(f.text("#tab-groups-status"), /无法读取/);
  assert.equal(f.window.document.body.textContent.includes(credential), false);
});

test("extension ID copy has success and failure feedback without extra permissions", async (t) => {
  const f = await fixture(t);
  let copied;
  Object.defineProperty(f.window.navigator, "clipboard", { configurable: true, value: { writeText: async (value) => { copied = value; } } });
  await f.gesture("#copy-extension-id");
  assert.equal(copied, f.window.chrome.runtime.id);
  assert.match(f.text("#copy-status"), /已复制/);
  Object.defineProperty(f.window.navigator, "clipboard", { value: undefined });
  await f.gesture("#copy-extension-id");
  assert.match(f.text("#copy-status"), /手动复制/);
});

test("website grants preserve protocol/host scope, deduplicate, revoke, and sync external Chrome changes", async (t) => {
  const f = await fixture(t);
  f.$("#website-url").value = "https://test.example:8443/path?q=1";
  await f.gesture("#website-permission", true);
  assert.deepEqual(Array.from(f.storage.pageOrigins), ["https://test.example/*"]);
  assert.deepEqual(Array.from(f.requests[0].origins), ["https://test.example/*"]);
  assert.match(f.text("#authorized-websites"), /已授权/);
  assert.match(f.text("#permission-status"), /授权成功/);
  await f.gesture("#website-permission", true);
  assert.equal(f.storage.pageOrigins.length, 1);
  await f.externalPermission("https://test.example/*");
  assert.match(f.text("#authorized-websites"), /未授权.*重新授权/);
  await f.externalPermission("https://test.example/*", true);
  assert.match(f.text("#authorized-websites"), /已授权/);
  await f.gesture("#authorized-websites button");
  assert.equal(f.storage.pageOrigins.length, 0);
  assert.equal(f.granted.has("https://test.example/*"), false);
  assert.equal(f.$("#websites-empty").hidden, false);
  assert.match(f.text("#permission-status"), /已撤销/);
});

test("website rejection, API errors, and invalid URLs do not create app consent", async (t) => {
  const f = await fixture(t);
  for (const url of ["chrome://settings", "file:///tmp/test", "https://user:password@example.com/", "javascript:alert(1)"]) {
    f.$("#website-url").value = url;
    await f.gesture("#website-permission", true);
    assert.match(f.text("#permission-status"), /仅支持/);
  }
  assert.equal(f.requests.length, 0);
  f.$("#website-url").value = "https://test.example";
  f.state.denied = true;
  await f.gesture("#website-permission", true);
  assert.match(f.text("#permission-status"), /未获得网站权限/);
  f.state.denied = false;
  f.fail.request = true;
  await f.gesture("#website-permission", true);
  assert.match(f.text("#permission-status"), /授权未完成/);
  assert.equal(f.storage.pageOrigins, undefined);
  assert.equal(f.text("#permission-status").includes(credential), false);
  assert.equal(f.$("#allow-website").disabled, false);
});

test("website storage failures cannot enable consent and failed revocation removes the app gate first", async (t) => {
  const f = await fixture(t);
  f.$("#website-url").value = "https://test.example";
  f.fail.set = true;
  await f.gesture("#website-permission", true);
  assert.equal(f.storage.pageOrigins, undefined);
  assert.match(f.text("#permission-status"), /授权未完成/);
  f.fail.set = false;
  await f.gesture("#website-permission", true);
  f.fail.remove = true;
  await f.gesture("#authorized-websites button");
  assert.equal(f.storage.pageOrigins.length, 0);
  assert.match(f.text("#permission-status"), /记录已移除.*撤销未完成/);
});

test("removing individual entries preserves broad grants and the required loopback host permission", async (t) => {
  const f = await fixture(t, { storage: { allWebsites: true, pageOrigins: ["https://kept.example/*", "http://127.0.0.1/*"] }, grants: allOrigins });
  assert.equal(f.$("#all-websites-notice").hidden, false);
  assert.match(f.text("#authorized-websites"), /全站授权覆盖/);
  await f.gesture("#authorized-websites button");
  assert.equal(f.removals.length, 0);
  assert.match(f.text("#permission-status"), /全站授权.*仍可访问/);
  f.storage.allWebsites = false;
  await f.gesture("#authorized-websites button");
  assert.equal(f.removals.length, 0);
  assert.equal(f.granted.has("http://127.0.0.1/*"), true);
});

const controls = [
  { key: "allWebsites", allow: "#allow-all-websites", revoke: "#revoke-all-websites", status: "#all-websites-status", feedback: "#all-websites-feedback", entries: allOrigins },
  { key: "groupAccess", allow: "#allow-tab-groups", revoke: "#revoke-tab-groups", status: "#tab-groups-status", feedback: "#tab-groups-feedback", entries: ["tabGroups"] },
  { key: "bookmarkAccess", allow: "#allow-bookmarks", revoke: "#revoke-bookmarks", status: "#bookmarks-status", feedback: "#bookmarks-feedback", entries: ["bookmarks"] },
];

for (const control of controls) {
  test(`${control.key}: gesture-only grants, denial/error feedback, external revocation and independent consent`, async (t) => {
    const storage = { pageOrigins: ["https://kept.example/*"], unrelated: true };
    const f = await fixture(t, { storage });
    assert.equal(f.requests.length, 0);
    f.state.denied = true;
    await f.gesture(control.allow);
    assert.notEqual(storage[control.key], true);
    assert.match(f.text(control.feedback), /未获得/);
    f.state.denied = false;
    f.fail.request = true;
    await f.gesture(control.allow);
    assert.match(f.text(control.feedback), /授权未完成/);
    f.fail.request = false;
    await f.gesture(control.allow);
    assert.equal(storage[control.key], true);
    assert.equal(f.$(control.status).dataset.tone, "success");
    assert.match(f.text(control.feedback), /授权成功/);
    const request = f.requests.at(-1);
    assert.deepEqual(Array.from(request.origins || request.permissions), control.entries);
    await f.externalPermission(control.entries[0]);
    assert.match(f.text(control.status), /未授权.*Chrome 权限已撤销/);
    assert.equal(storage[control.key], true); // Effective state depends on both gates.
    if (control.key === "allWebsites") assert.equal(f.$("#all-websites-notice").hidden, true);
    await f.externalPermission(control.entries[0], true);
    assert.equal(f.$(control.status).dataset.tone, "success");
    await f.gesture(control.revoke);
    assert.equal(storage[control.key], false);
    assert.match(f.text(control.feedback), /已撤销/);
    assert.notEqual(f.$(control.status).dataset.tone, "success");
    assert.deepEqual(storage.pageOrigins, ["https://kept.example/*"]);
    assert.equal(storage.unrelated, true);
    for (const other of controls.filter((item) => item !== control)) assert.equal(storage[other.key], undefined);
  });

  test(`${control.key}: neither a Chrome grant nor app consent alone grants access`, async (t) => {
    const f = await fixture(t, { grants: control.entries });
    assert.match(f.text(control.status), /未授权.*尚未在此确认/);
    assert.equal(f.requests.length, 0);
    f.storage[control.key] = true;
    f.emit("changed", { [control.key]: { newValue: true } }, "local");
    await flush();
    assert.equal(f.$(control.status).dataset.tone, "success");
    f.storage[control.key] = false;
    f.emit("changed", { [control.key]: { newValue: false } }, "local");
    await flush();
    assert.notEqual(f.$(control.status).dataset.tone, "success");
  });

  test(`${control.key}: storage and Chrome revocation failures leave explicit safe state`, async (t) => {
    const f = await fixture(t);
    f.fail.set = true;
    await f.gesture(control.allow);
    assert.notEqual(f.storage[control.key], true);
    assert.match(f.text(control.feedback), /授权未完成/);
    f.fail.set = false;
    await f.gesture(control.allow);
    f.fail.set = true;
    await f.gesture(control.revoke);
    assert.equal(f.storage[control.key], true);
    assert.equal(f.removals.length, 0);
    assert.match(f.text(control.feedback), /未能保存停用设置/);
    f.fail.set = false;
    for (const failure of ["remove", "removeFalse"]) {
      f.fail[failure] = true;
      await f.gesture(control.revoke);
      assert.equal(f.storage[control.key], false);
      assert.match(f.text(control.feedback), /已停用.*撤销未完成/);
      assert.notEqual(f.$(control.status).dataset.tone, "success");
      assert.equal(f.$(control.allow).disabled, false);
      f.fail[failure] = false;
    }
    await f.gesture(control.revoke);
    assert.match(f.text(control.feedback), /已撤销/);
  });
}

test("permission read errors remain distinct from denied permissions", async (t) => {
  const f = await fixture(t, { storage: { pageOrigins: ["https://test.example/*"] }, fail: { contains: true } });
  for (const control of controls) assert.match(f.text(control.status), /无法读取授权状态/);
  assert.match(f.text("#websites-error"), /无法读取网站授权列表/);
});

test("Options uses only packaged scripts/styles and accessible form labels", async (t) => {
  const f = await fixture(t);
  const document = f.window.document;
  for (const script of document.scripts) {
    assert.ok(script.getAttribute("src"));
    assert.equal(script.textContent.trim(), "");
    assert.ok(!script.getAttribute("src").includes(":"));
  }
  assert.equal(document.querySelector('link[rel="stylesheet"]').getAttribute("href"), "options.css");
  for (const input of document.querySelectorAll("input, select")) assert.ok(input.labels.length > 0, input.id);
  assert.equal(document.querySelectorAll("section.card").length, 3);
  assert.equal(document.querySelectorAll("[onclick], [onsubmit], [style]").length, 0);
});
