import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import { JSDOM } from "jsdom";
const { URL, structuredClone, setTimeout, clearTimeout, console } = globalThis;
const require = createRequire(import.meta.url);
const React = require("react");
const ts = require("typescript");

async function fixture(language = "en") {
  const dom = new JSDOM('<div id="root"></div>', { url: "https://gui.test/" });
  globalThis.window = dom.window; globalThis.document = dom.window.document;
  globalThis.HTMLElement = dom.window.HTMLElement;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const { createRoot } = require("react-dom/client");
  dom.window.localStorage.setItem("chat2local.gui.language.cache", language);
  const calls = [], notifications = [];
  const persisted = { security: { allowed_roots: ["C:/kept"] }, agent: { token_file: "C:/private/existing.token" } };
  const defaults = { browser: { enabled: false, port: 8766, extension_id: null, token_file: null }, hub: {}, agent: {} };
  const api = {
    credentialInfo: async () => ({ path: "C:/private/browser.token", state: "ready" }),
    manageToken: async (...args) => { calls.push(["manage", ...args]); return { path: "C:/private/new.token", state: "ready" }; },
    copyToken: async (...args) => { calls.push(["copy", ...args]); return { copied: true }; },
    browserStatus: async () => ({ state: "connected" }),
    readConfig: async () => ({ persisted, defaults, effective: defaults }),
    validateConfig: async (candidate) => { calls.push(["validate", candidate]); return { valid: true }; },
    saveConfig: async (candidate) => { calls.push(["save", candidate]); return { persisted: candidate, defaults, effective: defaults, restart_required: true }; },
  };
  const native = (tag) => (props) => React.createElement(tag, props, props.children);
  const mocks = {
    "@/lib/bridge": { coreApi: api, errorMessage: () => "Backend unavailable", desktopApi: {} },
    "sonner": { toast: { success: (text) => notifications.push(text), error() {} } },
    "@/components/ui/button": { Button: native("button") },
    "@/components/ui/input": { Input: native("input") },
    "@/components/ui/switch": { Switch: ({ checked, onCheckedChange, ...props }) => React.createElement("button", {
      ...props, type: "button", role: "switch", "aria-checked": checked, onClick: () => onCheckedChange(!checked),
    }) },
    "@/components/desktop-settings": { DesktopSettings: () => null },
    "@/components/ui/select": Object.fromEntries(["Select", "SelectContent", "SelectItem", "SelectTrigger", "SelectValue"].map((name) => [name, ({ children }) => React.createElement("div", null, children)])),
    "lucide-react": Object.fromEntries(["Save", "RotateCcw", "Plus", "X"].map((key) => [key, () => null])),
  };
  const cache = {};
  function load(name) {
    if (mocks[name]) return mocks[name];
    if (!name.startsWith("@/")) return require(name);
    if (cache[name]) return cache[name];
    const path = name.slice(2), extension = name === "@/lib/locale" ? "ts" : "tsx";
    const source = readFileSync(new URL(`../src/${path}.${extension}`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    const module = { exports: {} };
    cache[name] = module.exports;
    vm.runInNewContext(code, { require: load, exports: module.exports, module, window: dom.window,
      document: dom.window.document, localStorage: dom.window.localStorage, structuredClone,
      setTimeout, clearTimeout, console });
    return module.exports;
  }
  const root = createRoot(dom.window.document.getElementById("root"));
  const render = async (name, props) => {
    const module = load(name), Component = Object.values(module)[0];
    await React.act(async () => root.render(React.createElement(Component, props)));
  };
  const click = async (button) => { assert.ok(button); await React.act(async () => button.click()); };
  const button = (text) => [...globalThis.document.querySelectorAll("button")].find((node) => node.textContent === text);
  const close = async () => { await React.act(async () => root.unmount()); dom.window.close(); };
  return { api, calls, notifications, render, click, button, close, dom };
}

test("Browser token rotation needs a separate confirmation; copying uses the native backend", async () => {
  const f = await fixture();
  try {
    await f.render("@/components/token-manager", { purpose: "browser", path: "C:/private/browser.token", onPath() {} });
    assert.equal(f.calls.length, 0);
    await f.click(f.button("Regenerate token")); assert.equal(f.calls.length, 0);
    await f.click(f.button("Cancel")); assert.equal(f.calls.length, 0);
    await f.click(f.button("Copy token"));
    assert.deepEqual(f.calls[0], ["copy", "browser", "C:/private/browser.token"]);
    await f.click(f.button("Regenerate token"));
    await f.click(f.button("Confirm regeneration"));
    assert.deepEqual(f.calls[1], ["manage", "browser", "C:/private/browser.token", null, true, true]);
  } finally { await f.close(); }
});

test("Agent offers import or existing-file reuse, never generation", async () => {
  const f = await fixture();
  try {
    await f.render("@/components/token-manager", { purpose: "agent", path: null, onPath() {} });
    assert.equal(f.button("Generate or reuse token"), undefined);
    assert.equal(f.button("Regenerate token"), undefined);
    assert.equal(f.button("Import Hub token").disabled, true);
    assert.ok(globalThis.document.querySelector('input[type="password"]'));
    await f.click(f.button("Use existing token file")); assert.equal(f.calls.length, 0);
    const input = globalThis.document.querySelector('input[type="password"]');
    await React.act(async () => {
      Object.getOwnPropertyDescriptor(f.dom.window.HTMLInputElement.prototype, "value").set.call(input, "private-import-value");
      input.dispatchEvent(new f.dom.window.Event("input", { bubbles: true }));
    });
    await f.click(f.button("Import Hub token"));
    assert.deepEqual(f.calls[0], ["manage", "agent", null, "private-import-value", false, false]);
    assert.equal(input.value, "");
    assert.equal(globalThis.document.body.textContent.includes("private-import-value"), false);
  } finally { await f.close(); }
});

for (const mode of ["standalone", "hub", "agent"]) test(`Browser settings are available in ${mode} and preserve sparse config`, async () => {
  const f = await fixture();
  try {
    await f.render("@/pages/settings", { currentMode: mode });
    assert.ok(globalThis.document.getElementById("browser-enabled"));
    assert.equal(globalThis.document.getElementById("browser-port").value, "8766");
    assert.ok(globalThis.document.body.textContent.includes("Chrome extension is connected"));
    await f.click(globalThis.document.getElementById("browser-enabled"));
    await f.click(f.button("Save changes"));
    const saved = f.calls.find(([name]) => name === "save")[1];
    assert.equal(saved.browser.enabled, true);
    assert.deepEqual(saved.security.allowed_roots, ["C:/kept"]);
    assert.equal(saved.agent.token_file, "C:/private/existing.token");
    assert.ok(f.notifications.includes("Configuration saved · Restart required"));
    assert.equal(f.calls.filter(([name]) => name === "manage").length, 0);
  } finally { await f.close(); }
});

test("new Browser controls and confirmation messages are translated to Chinese", async () => {
  const f = await fixture("zh-CN");
  try {
    await f.render("@/components/token-manager", { purpose: "browser", path: null, onPath() {} });
    assert.ok(f.button("生成或复用 Token"));
    await f.click(f.button("重新生成 Token"));
    assert.ok(f.button("确认重新生成"));
    assert.ok(globalThis.document.body.textContent.includes("重新配对 Chrome 扩展"));
  } finally { await f.close(); }
});
