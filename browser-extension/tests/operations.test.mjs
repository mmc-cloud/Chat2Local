import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { page } from "./dom-helper.mjs";
const source = readFileSync(new URL("../browser-operations.js", import.meta.url), "utf8");

function fixture({ tabs = [{ id: 1, windowId: 7, title: "Test", url: "https://test.example/" }],
  consent = ["https://test.example/*"], granted = true, allWebsites = false, inject, send } = {}) {
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
  const context = vm.createContext({ chrome, URL, Date });
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

test("tabs list is bounded; selection uses native IDs and exact AND conditions", async () => {
  const f = fixture({ tabs: Array.from({ length: 8 }, (_, id) => ({ id, windowId: 7, title: "Same", url: "https://test.example/" })) });
  const listed = await f.call("browser.tabs.list", { limit: 3 });
  assert.equal(listed.result.tabs.length, 3);
  assert.equal(listed.result.total, 8);
  assert.equal(listed.result.truncated, true);
  const ambiguous = await f.call("browser.tabs.resolve", { tab: { title: "Same" } });
  assert.equal(ambiguous.error.code, "ambiguous_tab");
  assert.equal(ambiguous.error.details.candidates.length, 5);
  assert.equal((await f.call("browser.tabs.resolve", { tab: { tab_id: 3, title: "Same" } })).result.tab_id, 3);
  for (const tab of [{ tab_id: 99 }, { tab_id: 3, title: "Different" }, { url: "https://test.example" }]) {
    assert.equal((await f.call("browser.tabs.resolve", { tab })).error.code, "tab_not_found");
  }
  assert.equal(f.calls.messages.length, 0);
});

for (const url of ["chrome://settings", "chrome-extension://other/options.html", "file:///tmp/a",
  "https://chromewebstore.google.com/detail/anything", "https://chrome.google.com/webstore/detail/a"]) {
  test(`restricted page: ${url}`, async () => {
    const f = fixture({ tabs: [{ id: 1, url }] });
    assert.equal((await f.call("browser.page.inspect", { tab: { tab_id: 1 } })).error.code, "unsupported_page");
    assert.equal(f.calls.injected.length, 0);
  });
}

test("DOM access requires explicit consent AND current Chrome permission, including localhost", async () => {
  for (const options of [{ consent: [] }, { granted: false },
    { tabs: [{ id: 1, url: "http://127.0.0.1:18766/" }], consent: [] }]) {
    const f = fixture(options);
    assert.equal((await f.call("browser.page.click", { tab: { tab_id: 1 }, target: { name: "Go" } })).error.code, "permission_denied");
    assert.equal(f.calls.injected.length, 0);
    assert.equal((await f.call("browser.page.dom.inspect", { tab: { tab_id: 1 }, css: "div" })).error.code, "permission_denied");
    assert.equal(f.calls.injected.length, 0);
  }
});

test("all-site mode requires both the explicit flag and current broad Chrome grants", async () => {
  const f = fixture({ consent: [], allWebsites: true });
  assert.equal((await f.call("browser.page.inspect", { tab: { tab_id: 1 } })).ok, true);
  f.settings.allWebsites = false;
  assert.equal((await f.call("browser.page.inspect", { tab: { tab_id: 1 } })).error.code, "permission_denied");
  f.settings.allWebsites = true;
  f.chrome.permissions.contains = async () => false;
  assert.equal((await f.call("browser.page.inspect", { tab: { tab_id: 1 } })).error.code, "permission_denied");
  const restricted = fixture({ tabs: [{ id: 1, url: "chrome://settings" }], allWebsites: true });
  assert.equal((await restricted.call("browser.page.inspect", { tab: { tab_id: 1 } })).error.code, "unsupported_page");
  assert.equal(restricted.calls.injected.length, 0);
});

test("revoking all-site access during an element wait prevents the action before Chrome removal completes", async () => {
  let f;
  const p = page("", { authorize: (message) => f.authorize(message, {
    id: "test-extension", frameId: 0, tab: { id: 1 }, url: "https://test.example/",
  }) });
  f = fixture({ consent: [], allWebsites: true, send: (_, message) => p.dispatch(message) });
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  try {
    const pending = f.call("browser.page.click", { tab: { tab_id: 1 }, target: { name: "Later" }, wait_ms: 200 });
    p.window.setTimeout(() => {
      f.settings.allWebsites = false; // Chrome still reports broad host permission.
      p.document.body.innerHTML = "<button>Later</button>";
    }, 15);
    assert.equal((await pending).error.code, "permission_denied");
    assert.equal(clicks, 0);
  } finally { p.window.close(); }
});

test("background injects the packaged top-frame module and sends to its documentId; actual DOM executes", async () => {
  let f;
  const p = page('<label for="q">Query</label><input id="q"><button>Go</button>', {
    authorize: (message) => f.authorize(message, { id: "test-extension", frameId: 0, tab: { id: 1 }, url: "https://test.example/" }),
  });
  f = fixture({ send: (id, message) => p.dispatch(message) });
  let clicks = 0;
  p.document.querySelector("button").addEventListener("click", () => clicks++);
  try {
    const inspected = await f.call("browser.page.inspect", { tab: { title: "Test" }, target: { role: "textbox" } });
    assert.equal(inspected.result.elements[0].name, "Query");
    assert.equal((await f.call("browser.page.fill", { tab: { tab_id: 1 }, target: { label: "Query" }, value: "native text" })).result.status, "filled");
    assert.equal(p.document.querySelector("input").value, "native text");
    assert.equal((await f.call("browser.page.click", { tab: { tab_id: 1 }, target: { role: "button", name: "Go" } })).result.status, "clicked");
    assert.equal(clicks, 1);
    assert.deepEqual(Array.from(f.calls.injected[0].files), ["content-script.js"]);
    assert.deepEqual(Array.from(f.calls.injected[0].target.frameIds), [0]);
    assert.equal(f.calls.injected[0].world, "ISOLATED");
    assert.equal(f.calls.messages[0][2].documentId, "doc-1");
    p.document.body.insertAdjacentHTML("beforeend", '<div class="custom">Send</div>');
    const dom = await f.call("browser.page.dom.inspect", { tab: { title: "Test" }, css: "div.custom" });
    assert.equal(dom.result.elements[0].tag, "div");
    assert.equal(dom.result.elements[0].text, "Send");
    assert.equal(f.calls.messages.at(-1)[1].operation, "dom.inspect");
    assert.equal((await f.call("browser.page.click", { tab: { tab_id: 1 }, target: { css: dom.result.elements[0].css } })).result.status, "clicked");
  } finally { p.dom.window.close(); }
});

test("navigation, injection failure, tab close, revocation and expired injection are classified without retry", async () => {
  const args = { tab: { tab_id: 1 }, target: { name: "Go" } };
  let f;
  f = fixture({ inject: async () => { f.tabs[0].url = "https://different.example/"; return [{ frameId: 0, documentId: "doc-1" }]; } });
  assert.equal((await f.call("browser.page.click", args)).error.code, "page_changed");
  assert.equal(f.calls.messages.length, 0);
  f = fixture({ inject: async () => { throw Error("PRIVATE-EXCEPTION"); } });
  assert.equal((await f.call("browser.page.click", args)).error.code, "injection_failed");
  f = fixture({ send: async () => { f.tabs.length = 0; throw Error("closed"); } });
  assert.equal((await f.call("browser.page.click", args)).error.code, "tab_not_found");
  assert.equal(f.calls.messages.length, 1);
  f = fixture({ inject: async () => { f.consent.length = 0; return [{ frameId: 0, documentId: "doc-1" }]; } });
  assert.equal((await f.call("browser.page.click", args)).error.code, "permission_denied");
  f = fixture({ inject: async () => { await new Promise((resolve) => setTimeout(resolve, 20)); return [{ frameId: 0, documentId: "doc-1" }]; } });
  assert.equal((await f.call("browser.page.click", args, Date.now() + 10)).error.code, "request_expired");
  assert.equal(f.calls.messages.length, 0);
});

test("all page operations recheck the full tab selector after injection", async () => {
  for (const capability of ["browser.page.inspect", "browser.page.dom.inspect", "browser.page.click", "browser.page.fill"]) {
    for (const tab of [{ title: "Test" }, { tab_id: 1, title: "Test" },
      { url: "https://test.example/", title: "Test" }]) {
      let f;
      f = fixture({ inject: async () => {
        f.tabs[0].title = "Changed";
        return [{ frameId: 0, documentId: "doc-1" }];
      } });
      const args = { tab, ...(capability === "browser.page.dom.inspect" ? { css: "div" } : { target: { name: "Go" } }),
        ...(capability.endsWith("fill") ? { value: "text" } : {}) };
      assert.equal((await f.call(capability, args)).error?.code, "page_changed", `${capability} ${JSON.stringify(tab)}`);
      assert.equal(f.calls.messages.length, 0);
    }
  }
  // A title change alone does not invalidate an explicit ID-only selector.
  let f;
  f = fixture({ inject: async () => {
    f.tabs[0].title = "Changed";
    return [{ frameId: 0, documentId: "doc-1" }];
  } });
  assert.equal((await f.call("browser.page.click", { tab: { tab_id: 1 }, target: { name: "Go" } })).ok, true);
});

test("tab selector is checked after the final asynchronous permission lookup", async () => {
  const f = fixture();
  let checks = 0;
  f.chrome.permissions.contains = async () => {
    if (++checks === 2) f.tabs[0].title = "Changed";
    return true;
  };
  assert.equal((await f.call("browser.page.click", { tab: { title: "Test" }, target: { name: "Go" } })).error?.code, "page_changed");
  assert.equal(f.calls.messages.length, 0);
});

test("DOM authorization rechecks the original selector after an element wait", async () => {
  let f;
  const p = page("", { authorize: (message) => f.authorize(message, {
    id: "test-extension", frameId: 0, tab: { id: 1 }, url: "https://test.example/",
  }) });
  f = fixture({ send: (_, message) => p.dispatch(message) });
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  try {
    const pending = f.call("browser.page.click", { tab: { title: "Test" }, target: { name: "Later" }, wait_ms: 200 });
    p.window.setTimeout(() => {
      f.tabs[0].title = "Changed";
      p.document.body.innerHTML = "<button>Later</button>";
    }, 15);
    assert.equal((await pending).error?.code, "page_changed");
    assert.equal(clicks, 0);
  } finally { p.window.close(); }
});

test("authorization callback rejects wrong senders, frames and changed URL", async () => {
  const f = fixture();
  for (const sender of [{ id: "other", frameId: 0, tab: { id: 1 }, url: f.tabs[0].url },
    { id: "test-extension", frameId: 1, tab: { id: 1 }, url: f.tabs[0].url },
    { id: "test-extension", frameId: 0, tab: { id: 1 }, url: "https://changed.example/" }]) {
    assert.equal((await f.authorize({ type: "chat2local.dom.authorize", expected_url: f.tabs[0].url, tab: { tab_id: 1 } }, sender)).ok, false);
  }
});

test("website permission patterns never accept wildcard hosts or embedded credentials", () => {
  const f = fixture();
  for (const url of ["https://*.example.com", "https://*", "https://user:pass@example.com"]) {
    assert.equal(f.api.pagePattern(url), null);
  }
  assert.equal(f.api.pagePattern("https://specific.example:8443/a"), "https://specific.example/*");
});
