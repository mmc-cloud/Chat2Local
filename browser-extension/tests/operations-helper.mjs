import { page } from "./dom-helper.mjs";
import { readFileSync } from "node:fs";
import vm from "node:vm";
const source = readFileSync(new URL("../browser-operations.js", import.meta.url), "utf8");

export function fixture({ tabs = [{ id: 1, windowId: 7, title: "Test", url: "https://test.example/" }],
  consent = ["https://test.example/*"], granted = true, allWebsites = false, inject, send, cryptoApi = crypto } = {}) {
  const calls = { injected: [], messages: [] };
  let listener;
  const settings = { pageOrigins: consent, allWebsites };
  const chrome = {
    runtime: { id: "test-extension", onMessage: { addListener(fn) { listener = fn; } } },
    storage: { local: { get: async () => settings } },
    permissions: { contains: async () => granted },
    tabs: {
      query: async () => tabs.map((tab) => ({ ...tab })),
      get: async (id) => { const tab = tabs.find((tab) => tab.id === id); if (!tab) throw Error("closed"); return { ...tab }; },
      create: async (args) => ({ id: 99, windowId: 7, url: args.url }),
      sendMessage: async (...args) => { calls.messages.push(args); return send ? send(...args) : { ok: true, result: { status: "clicked" } }; },
    },
    scripting: { executeScript: async (args) => { calls.injected.push(args); return inject ? inject(args) : [{ frameId: 0, documentId: "doc-1" }]; } },
  };
  const context = vm.createContext({ chrome, URL, Date, crypto: cryptoApi, TextEncoder, btoa, atob });
  vm.runInContext(readFileSync(new URL("../browser-safety.js", import.meta.url), "utf8"), context);
  vm.runInContext(source, context);
  const api = context.Chat2LocalBrowser;
  api.installAuthorizationHandler();
  const call = (capability, args, expiresAt = Date.now() + 10000) => api.execute({ capability,
    arguments: args, expires_at: expiresAt });
  const authorize = (message, sender) => new Promise((resolve) => {
    if (!listener(message, sender, resolve)) resolve({ ok: false });
  });
  return { api, call, chrome, calls, authorize, tabs, consent, settings };
}

export function browserPage(html, options = {}) {
  let f;
  const url = options.url ?? "https://test.example/";
  const p = page(html, { url, authorize: (message) => f.authorize(message, {
    id: "test-extension", frameId: 0, tab: { id: 1 }, url: p.window.location.href,
  }) });
  f = fixture({ tabs: [{ id: 1, windowId: 7, title: "Test", url }], send: (_, message) => p.dispatch(message), ...options });
  const read = (args = {}) => f.call("browser.page.read", { tab: { tab_id: 1 }, ...args });
  const call = (operation, args = {}, extra = {}) => p.call(operation, { tab: { tab_id: 1 }, ...args }, extra);
  return { ...p, call, f, read };
}
