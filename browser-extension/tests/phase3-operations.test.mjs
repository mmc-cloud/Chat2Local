import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { browserPage, fixture } from "./operations-helper.mjs";

const schemas = JSON.parse(readFileSync(new URL("../capability-schemas.json", import.meta.url), "utf8"));
const tab = { tab_id: 1 };
const actions = [
  ["double_click", { target: { id: "button" } }], ["hover", { target: { id: "button" } }],
  ["focus", { target: { id: "input" } }], ["select", { target: { id: "select" }, options: [{ value: "b" }] }],
  ["set_checked", { target: { id: "check" }, checked: true }], ["scroll", { container: "page", delta_y: 100 }],
  ["press_key", { target: { id: "input" }, key: "Escape" }], ["wait_for", { target: { id: "button" }, state: "visible", timeout_ms: 0 }],
];

for (const [operation, extra] of actions) {
  const capability = `browser.page.${operation}`, args = { tab, ...extra };
  test(`${operation}: consent, restricted page, original TabSelector and document loss never replay`, async () => {
    let f = fixture({ consent: [] });
    assert.equal((await f.call(capability, args)).error.code, "permission_denied");
    assert.equal(f.calls.injected.length, 0);
    f = fixture({ granted: false });
    assert.equal((await f.call(capability, args)).error.code, "permission_denied");
    f = fixture({ tabs: [{ id: 1, url: "chrome://settings" }] });
    assert.equal((await f.call(capability, args)).error.code, "unsupported_page");
    f = fixture({ inject: async () => { f.tabs[0].title = "Changed"; return [{ frameId: 0, documentId: "doc-1" }]; } });
    assert.equal((await f.call(capability, { ...args, tab: { tab_id: 1, title: "Test" } })).error.code, "page_changed");
    assert.equal(f.calls.messages.length, 0);
    let fail = true;
    f = fixture({ send: async () => { if (fail) throw Error("PRIVATE-DISCONNECTED"); return { ok: true, result: {} }; } });
    assert.equal((await f.call(capability, args)).error.code, "page_changed");
    assert.equal(f.calls.messages.length, 1);
    fail = false;
    assert.equal((await f.call(capability, args)).ok, true);
    assert.equal(f.calls.messages.length, 2); // A second explicit call, not replay.
  });
  test(`${operation}: actual packaged result exactly matches its discovered success contract`, async () => {
    const p = browserPage('<button id="button">Run</button><input id="input"><input id="check" type="checkbox">'
      + '<select id="select"><option value="a">A</option><option value="b">B</option></select>');
    Object.defineProperty(p.document, "scrollingElement", { value: p.document.documentElement });
    p.document.documentElement.scrollBy = ({ top }) => { p.document.documentElement.scrollTop += top; };
    try {
      const response = await p.f.call(capability, args);
      assert.equal(response.ok, true, JSON.stringify(response));
      const contract = schemas.capabilities.find((entry) => entry.name === capability).result_contract;
      assert.deepEqual(Object.keys(response.result).sort(), Object.keys(contract).sort());
      assert.equal(p.f.calls.messages[0][2].documentId, "doc-1");
      assert.equal(p.f.calls.messages[0][1].operation, operation);
    } finally { p.window.close(); }
  });
  for (const change of ["consent", "selector", "expiry"]) test(`${operation}: final ${change} change is checked after a completed operation, without replay`, async () => {
    let f;
    let now = Date.now();
    const expires = now + 1000;
    f = fixture({ dateApi: { now: () => now }, send: async () => {
      if (change === "consent") f.settings.pageOrigins = [];
      if (change === "selector") f.tabs[0].title = "Changed";
      if (change === "expiry") now = expires;
      return { ok: true, result: {} };
    } });
    const result = await f.call(capability, { ...args, tab: { tab_id: 1, title: "Test" } }, expires);
    assert.equal(result.error?.code, { consent: "permission_denied", selector: "page_changed", expiry: "request_expired" }[change]);
    assert.equal(f.calls.messages.length, 1);
  });
}

test("Phase 3 argument validators reject extras, unknown state, option shapes, ambiguous scrolling and malformed keys", async () => {
  const f = fixture();
  for (const [operation, extra] of [
    ["hover", { target: { id: "x" }, force: true }], ["double_click", { target: { id: "x" }, wait_ms: -1 }],
    ["focus", { target: {} }], ["select", { target: { id: "x" }, options: [{}] }],
    ["select", { target: { id: "x" }, options: [{ index: true }] }],
    ["set_checked", { target: { id: "x" }, checked: "true" }], ["scroll", { delta_y: 1 }],
    ["scroll", { container: "page", target: { id: "x" }, delta_y: 1 }], ["scroll", { container: "page", delta_y: 0 }],
    ["scroll", { container: "element", delta_y: 1 }], ["press_key", { target: { id: "x" }, key: "Ctrl+a" }],
    ["press_key", { target: { id: "x" }, key: "a", modifiers: ["Control", "Control"] }],
    ["wait_for", { target: { id: "x" }, state: "missing" }], ["wait_for", { target: { id: "x" }, timeout_ms: 10001 }],
  ]) assert.equal((await f.call(`browser.page.${operation}`, { tab, ...extra })).error.code, "invalid_arguments");
  assert.equal(f.calls.injected.length, 0);
});
