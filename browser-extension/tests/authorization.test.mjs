import assert from "node:assert/strict";
import test from "node:test";
import { page } from "./dom-helper.mjs";
import { browserPage, fixture } from "./operations-helper.mjs";

const code = (result, expected) => assert.equal(result.error?.code, expected, JSON.stringify(result));
const sender = (extra = {}) => ({ id: "test-extension", frameId: 0, tab: { id: 1 },
  url: "https://test.example/", documentId: "doc-1", documentLifecycle: "active", ...extra });
const request = (extra = {}) => ({ type: "chat2local.dom.authorize", expected_url: "https://test.example/",
  expected_document_id: "doc-1", tab: { tab_id: 1 }, ...extra });

for (const allWebsites of [false, true]) test(`same-document SPA routes retain authorization, allWebsites=${allWebsites}`, async () => {
  const p = browserPage("<h1>Analytics</h1>", { allWebsites, ...(allWebsites ? { consent: [] } : {}) });
  try {
    assert.equal((await p.read()).ok, true);
    for (const [method, path] of [["pushState", "/analytics?range=week"], ["replaceState", "/settings#chart"]]) {
      p.window.history[method](null, "", path);
      p.f.tabs[0].url = p.window.location.href;
      const result = await p.read();
      assert.equal(result.ok, true, JSON.stringify(result));
      assert.equal(p.sender.url, "https://test.example/"); // Real Chromium retains this original route.
      assert.equal(p.f.calls.messages.at(-1)[1].expected_document_id, "doc-1");
      assert.equal(p.f.calls.messages.at(-1)[2].documentId, "doc-1");
    }
    // Both the site's explicit consent and the Chrome host grant remain required.
    if (allWebsites) p.f.settings.allWebsites = false;
    else p.f.consent.length = 0;
    code(await p.read(), "permission_denied");
    p.f.settings.allWebsites = true;
    p.f.chrome.permissions.contains = async () => false;
    code(await p.read(), "permission_denied");
  } finally { p.window.close(); }
});

test("authorization accepts an unchanged authorized top document", async () => {
  assert.equal((await fixture().authorize(request(), sender())).ok, true);
});

for (const [name, originSender, message] of [
  ["cross-origin sender", { url: "https://other.example/" }, {}],
  ["different port", { url: "https://test.example:8443/" }, {}],
  ["different protocol", { url: "http://test.example/" }, {}],
  ["missing sender URL", { url: undefined }, {}],
  ["new document at the same URL", { documentId: "doc-2" }, {}],
  ["missing sender document", { documentId: undefined }, {}],
  ["missing request document", {}, { expected_document_id: undefined }],
  ["cached document", { documentLifecycle: "cached" }, {}],
  ["prerender document", { documentLifecycle: "prerender" }, {}],
  ["wrong tab selector", {}, { tab: { tab_id: 2 } }],
  ["invalid expected URL", {}, { expected_url: "not a URL" }],
]) test(`authorization identifies ${name} as page_changed`, async () => {
  code(await fixture({ allWebsites: true }).authorize(request(message), sender(originSender)), "page_changed");
});

test("extension identity, top frame and sender tab are still required", async () => {
  const f = fixture();
  for (const extra of [{ id: "other-extension" }, { frameId: 1 }, { tab: undefined }]) {
    assert.equal((await f.authorize(request(), sender(extra))).ok, false);
  }
});

for (const change of ["route", "cross-origin", "pending", "title"]) test(`live tab ${change} during authorization returns page_changed`, async () => {
  const f = fixture({ allWebsites: true });
  f.chrome.permissions.contains = async () => {
    if (change === "route") f.tabs[0].url += "settings";
    if (change === "cross-origin") f.tabs[0].url = "https://other.example/";
    if (change === "pending") f.tabs[0].pendingUrl = "https://other.example/";
    if (change === "title") f.tabs[0].title = "Changed";
    return true;
  };
  code(await f.authorize(request({ tab: { tab_id: 1, title: "Test" } }), sender()), "page_changed");
});

for (const change of ["consent", "Chrome grant"]) test(`secondary authorization catches actual revocation of ${change}`, async () => {
  const p = browserPage('<input type="checkbox" id="x">', { consent: [], allWebsites: true });
  let clicks = 0;
  p.document.querySelector("#x").addEventListener("click", () => {
    clicks++;
    if (change === "consent") p.f.settings.allWebsites = false;
    else p.f.chrome.permissions.contains = async () => false;
  });
  try {
    code(await p.f.call("browser.page.set_checked", { tab: { tab_id: 1 }, target: { id: "x" }, checked: true }), "permission_denied");
    assert.equal(clicks, 1); // Completion never replays the action.
  } finally { p.window.close(); }
});

test("secondary authorization rejects a replacement document at the same URL without replay", async () => {
  const p = browserPage('<input type="checkbox" id="x">');
  let clicks = 0;
  p.document.querySelector("#x").addEventListener("click", () => { clicks++; p.sender.documentId = "doc-2"; });
  try {
    code(await p.f.call("browser.page.set_checked", { tab: { tab_id: 1 }, target: { id: "x" }, checked: true }), "page_changed");
    assert.equal(clicks, 1);
  } finally { p.window.close(); }
});

for (const reply of ["denied", "missing", "rejected"]) test(`URL changes while awaiting a ${reply} authorization reply are page_changed`, async () => {
  let p;
  p = page("<h1>Analytics</h1>", { authorize: async () => {
    p.window.history.pushState(null, "", "/new-route");
    if (reply === "rejected") throw Error("channel closed");
    return reply === "denied" ? { ok: false, error: { code: "permission_denied" } } : undefined;
  } });
  try { code(await p.call("read"), "page_changed"); } finally { p.window.close(); }
});

for (const reply of [undefined, { ok: false }, { ok: false, error: { code: "unexpected" } }, "rejected"]) {
  test(`unknown authorization failures do not invent permission denial: ${JSON.stringify(reply)}`, async () => {
    const p = page("<h1>Analytics</h1>", { authorize: async () => {
      if (reply === "rejected") throw Error("channel closed");
      return reply;
    } });
    try { code(await p.call("read"), "browser_operation_failed"); } finally { p.window.close(); }
  });
}

test("authorization backend errors are not reported as permission denial", async () => {
  const f = fixture();
  f.chrome.storage.local.get = async () => { throw Error("storage unavailable"); };
  code(await f.authorize(request(), sender()), "browser_operation_failed");
});
