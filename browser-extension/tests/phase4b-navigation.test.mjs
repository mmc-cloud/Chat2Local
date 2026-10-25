import assert from "node:assert/strict";
import test from "node:test";
import { browser } from "./phase4b-helper.mjs";

const destination = "https://next.example/";
async function observationId(f, id = 1) {
  const result = await f.status(id);
  assert.equal(result.ok, true);
  return result.result.navigation.navigation_id;
}

test("status observes existence, load state and sanitized URLs without page side effects", async () => {
  const f = browser();
  f.tabs[0].pendingUrl = "https://pending.example/?token=PRIVATE-URL";
  const result = await f.status();
  assert.equal(result.result.exists, true);
  assert.equal(result.result.loading_status, "complete");
  assert.equal(result.result.pending_url, "https://pending.example/");
  assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
  assert.equal(result.result.navigation, null);
  f.close(1);
  const missing = await f.status();
  assert.equal(missing.result.exists, false);
  assert.equal(missing.result.loading_status, "closed");
  assert.equal(f.mutations.length, 0);
  assert.equal(f.calls.injected.length, 0);
});

test("old complete cannot qualify a new navigation which has not started", async () => {
  const f = browser();
  await f.go(1, "reload");
  const navigation_id = await observationId(f);
  const result = await f.wait({ condition: "navigation_complete", url: f.tabs[0].url, navigation_id, timeout_ms: 0 });
  assert.equal(result.result.status, "unmet");
  assert.equal(result.result.reason, "navigation_unproven");
  assert.equal(result.result.observation.navigation.completion_observed, false);
  assert.equal(f.mutations.length, 1);
});

test("loading then complete qualifies only the exact target navigation and URL", async () => {
  const f = browser();
  await f.go();
  const navigation_id = await observationId(f);
  const waiting = f.wait({ condition: "navigation_complete", url: destination, navigation_id });
  setTimeout(() => {
    f.emit(1, { status: "loading", pendingUrl: destination });
    f.emit(1, { url: destination, pendingUrl: undefined, status: "complete" });
  }, 10);
  const result = await waiting;
  assert.equal(result.result.status, "matched");
  assert.equal(result.result.observation.navigation.completion_observed, true);
  assert.equal(f.mutations.length, 1);
});

test("events before wait registration are retained without a missed-event timeout", async () => {
  const f = browser();
  await f.go(1, "reload");
  f.emit(1, { status: "loading" }); f.emit(1, { status: "complete" });
  const navigation_id = await observationId(f);
  const result = await f.wait({ condition: "navigation_complete", url: f.tabs[0].url, navigation_id, timeout_ms: 0 });
  assert.equal(result.result.status, "matched");
  assert.equal(f.mutations.length, 1);
});

test("continuous loading ends as unmet with a final observation, never another navigation", async () => {
  const f = browser();
  await f.go(); f.emit(1, { status: "loading", pendingUrl: destination });
  const result = await f.wait({ condition: "navigation_complete", url: destination, navigation_id: await observationId(f), timeout_ms: 25 });
  assert.equal(result.result.status, "unmet");
  assert.equal(result.result.observation.loading_status, "loading");
  assert.equal(f.mutations.length, 1);
});

test("redirect chains and SPA URL changes use explicit URL conditions", async () => {
  const f = browser(); await f.go();
  f.emit(1, { status: "loading", pendingUrl: destination });
  f.emit(1, { url: "https://redirect.example/" });
  f.emit(1, { url: "https://final.example/", status: "complete", pendingUrl: undefined });
  const id = await observationId(f);
  assert.equal((await f.wait({ condition: "navigation_complete", url: "https://final.example/", navigation_id: id, timeout_ms: 0 })).result.status, "matched");
  assert.equal((await f.wait({ condition: "url", url: destination, timeout_ms: 0 })).result.status, "unmet");
  const baseline_url = f.tabs[1].url;
  f.emit(2, { url: "https://spa.example/route" });
  const changed = await f.wait({ tab_id: 2, condition: "url_changed", baseline_url, timeout_ms: 0 });
  assert.equal(changed.result.status, "matched");
  assert.equal(changed.result.observation.navigation, null);
});

test("current_complete reports the existing document without claiming a new navigation", async () => {
  const f = browser();
  const result = await f.wait({ condition: "current_complete", url: f.tabs[0].url, timeout_ms: 0 });
  assert.equal(result.result.status, "matched");
  assert.equal(result.result.observation.navigation, null);
  assert.equal(f.mutations.length, 0);
});

test("tab closure, superseded navigation and missing receipts are distinguishable", async () => {
  const f = browser(); await f.go();
  const old = await observationId(f);
  await f.go(1, "reload");
  assert.equal((await f.wait({ condition: "navigation_complete", url: destination, navigation_id: old })).result.reason, "navigation_overridden");
  assert.equal((await f.wait({ tab_id: 2, condition: "navigation_complete", url: destination, navigation_id: old })).result.reason, "navigation_missing");
  f.close(1);
  assert.equal((await f.wait({ condition: "url", url: destination })).result.reason, "tab_closed");
  assert.equal(f.mutations.length, 2);
});

test("a navigation after completion invalidates that receipt; other tabs do not interfere", async () => {
  const f = browser(); await f.go();
  f.emit(1, { status: "loading" }); f.emit(1, { url: destination, status: "complete" });
  const id = await observationId(f);
  await f.go(2); f.emit(2, { url: "https://other.example/", status: "loading" });
  assert.equal((await f.wait({ condition: "navigation_complete", url: destination, navigation_id: id, timeout_ms: 0 })).result.status, "matched");
  f.emit(1, { pendingUrl: "https://overriding.example/", status: "loading" });
  assert.equal((await f.wait({ condition: "navigation_complete", url: destination, navigation_id: id })).result.reason, "navigation_overridden");
});

test("an already-loading same-URL page is not enough proof of a new reload", async () => {
  const f = browser(); f.tabs[0].status = "loading";
  await f.go(1, "reload");
  f.emit(1, { status: "complete" });
  const result = await f.wait({ condition: "navigation_complete", url: f.tabs[0].url, navigation_id: await observationId(f), timeout_ms: 0 });
  assert.equal(result.result.reason, "navigation_unproven");
});

test("a failed Chrome call is not retried by status or wait", async () => {
  const f = browser(); let attempts = 0;
  f.chrome.tabs.reload = async () => { attempts++; throw Error("PRIVATE-ERROR"); };
  assert.equal((await f.go(1, "reload")).error.code, "browser_operation_failed");
  const result = await f.wait({ condition: "navigation_complete", url: f.tabs[0].url, navigation_id: await observationId(f) });
  assert.equal(result.result.reason, "navigation_failed"); assert.equal(attempts, 1);
  assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
});

test("unwitnessed requested destination cannot be invented from an unrelated completed URL", async () => {
  const f = browser(); await f.go();
  f.emit(1, { url: "https://unrelated.example/", status: "complete" });
  const result = await f.wait({ condition: "navigation_complete", url: f.tabs[0].url, navigation_id: await observationId(f), timeout_ms: 0 });
  assert.equal(result.result.status, "unmet");
  assert.equal(result.result.observation.navigation.completion_observed, false);
});

test("re-query detects a post-completion navigation even if its loading event was missed", async () => {
  const f = browser(); await f.go(1, "reload");
  f.emit(1, { status: "loading" }); f.emit(1, { status: "complete" });
  const id = await observationId(f);
  f.tabs[0].status = "loading"; // Native state changed without a delivered callback.
  assert.equal((await f.status(1, id)).result.navigation.state, "overridden");
});

test("unknown URL does not satisfy a URL-change condition", async () => {
  const f = browser(); const baseline_url = f.tabs[0].url;
  delete f.tabs[0].url;
  const result = await f.wait({ condition: "url_changed", baseline_url, timeout_ms: 0 });
  assert.equal(result.result.status, "unmet");
});

test("receipt expiry and outer request deadlines remain authoritative", async () => {
  let now = 100;
  const f = browser({ dateApi: { now: () => now } });
  await f.go(1, "reload"); const id = await observationId(f);
  now += 120001;
  assert.equal((await f.status(1, id)).result.navigation.state, "missing");
  const get = f.chrome.tabs.get;
  f.chrome.tabs.get = async (tabId) => { const tab = await get(tabId); now += 100; return tab; };
  const result = await f.call("browser.navigation.wait", { tab_id: 1, condition: "current_complete", url: f.tabs[0].url }, now + 50);
  assert.equal(result.error.code, "request_expired");
  assert.equal(f.mutations.length, 1);
});

test("a fresh loading cycle to another pending destination overrides an in-flight navigation", async () => {
  const f = browser(); await f.go();
  f.emit(1, { status: "loading", pendingUrl: destination });
  const id = await observationId(f);
  f.emit(1, { status: "loading", pendingUrl: "https://manual.example/" });
  const result = await f.wait({ condition: "navigation_complete", url: destination, navigation_id: id });
  assert.equal(result.result.reason, "navigation_overridden");
  assert.equal(f.mutations.length, 1);
});

test("go returns its own receipt so overlapping same-URL attempts cannot be confused", async () => {
  const f = browser();
  const first = (await f.go(1, "reload")).result;
  const second = (await f.go(1, "reload")).result;
  assert.notEqual(first.navigation_id, second.navigation_id);
  f.emit(1, { status: "loading" }); f.emit(1, { status: "complete" });
  assert.equal((await f.wait({ condition: "navigation_complete", url: f.tabs[0].url, navigation_id: first.navigation_id, timeout_ms: 0 })).result.reason, "navigation_overridden");
  assert.equal((await f.wait({ condition: "navigation_complete", url: f.tabs[0].url, navigation_id: second.navigation_id, timeout_ms: 0 })).result.status, "matched");
  assert.equal(f.mutations.length, 2);
});

const privateURL = "https://test.example/?token=PRIVATE-SECRET#token=PRIVATE-FRAGMENT";
async function urlBaseline(f, tabId = 1) {
  const response = await f.status(tabId);
  assert.equal(response.ok, true);
  assert.equal(JSON.stringify(response).includes("PRIVATE-"), false);
  assert.match(response.result.url_baseline_id, /^url_[0-9a-f-]{36}$/);
  return response.result.url_baseline_id;
}

test("opaque baseline compares raw URLs without mistaking redaction for navigation", async () => {
  const f = browser(); f.tabs[0].url = privateURL;
  const url_baseline_id = await urlBaseline(f);
  const result = await f.wait({ condition: "url_changed", url_baseline_id, timeout_ms: 0 });
  assert.equal(result.result.status, "unmet"); assert.equal(result.result.reason, "timeout");
  assert.equal(result.result.observation.url_baseline_id, null);
  assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
  assert.notEqual(await urlBaseline(f), url_baseline_id);
  assert.equal(f.mutations.length, 0); assert.equal(f.calls.injected.length, 0);
});

test("URL baseline detects real navigation, query-only and fragment-only changes", async () => {
  for (const url of ["https://test.example/changed?token=PRIVATE-SECRET#token=PRIVATE-FRAGMENT",
    "https://test.example/?token=PRIVATE-NEW#token=PRIVATE-FRAGMENT",
    "https://test.example/?token=PRIVATE-SECRET#token=PRIVATE-NEW"]) {
    const f = browser(); f.tabs[0].url = privateURL;
    const url_baseline_id = await urlBaseline(f);
    f.emit(1, { url });
    const result = await f.wait({ condition: "url_changed", url_baseline_id, timeout_ms: 0 });
    assert.equal(result.result.status, "matched");
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
    assert.equal(f.mutations.length, 0);
  }
});

test("URL baselines reject wrong tabs, unknown IDs, restart loss and expired records", async () => {
  let now = 100;
  const f = browser({ dateApi: { now: () => now } }); f.tabs[0].url = privateURL;
  const url_baseline_id = await urlBaseline(f);
  for (const result of [
    await f.wait({ tab_id: 2, condition: "url_changed", url_baseline_id, timeout_ms: 0 }),
    await f.wait({ condition: "url_changed", url_baseline_id: "url_00000000-0000-0000-0000-000000000000", timeout_ms: 0 }),
    await browser().wait({ condition: "url_changed", url_baseline_id, timeout_ms: 0 }),
  ]) {
    assert.equal(result.error.code, "url_baseline_unavailable");
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
  }
  now += 120000;
  assert.equal((await f.wait({ condition: "url_changed", url_baseline_id, timeout_ms: 0 })).error.code, "url_baseline_unavailable");
  assert.equal(f.mutations.length, 0);
});

test("baseline store evicts oldest entries without consuming or renewing retained IDs", async () => {
  const f = browser(); const first = await urlBaseline(f);
  for (let i = 0; i < 127; i++) await f.status();
  assert.equal((await f.wait({ condition: "url_changed", url_baseline_id: first, timeout_ms: 0 })).result.status, "unmet");
  const retained = await urlBaseline(f);
  assert.equal((await f.wait({ condition: "url_changed", url_baseline_id: first, timeout_ms: 0 })).error.code, "url_baseline_unavailable");
  for (let i = 0; i < 2; i++) assert.equal((await f.wait({ condition: "url_changed", url_baseline_id: retained, timeout_ms: 0 })).result.status, "unmet");
});

test("concurrent waits keep independent tab baselines and may share one baseline", async () => {
  const f = browser(); f.tabs[0].url = privateURL; f.tabs[1].url = privateURL;
  const a = await urlBaseline(f), b = await urlBaseline(f, 2);
  const waiting = Promise.all([
    f.wait({ condition: "url_changed", url_baseline_id: a }),
    f.wait({ condition: "url_changed", url_baseline_id: a }),
    f.wait({ tab_id: 2, condition: "url_changed", url_baseline_id: b, timeout_ms: 20 }),
  ]);
  setTimeout(() => f.emit(1, { url: "https://test.example/?token=PRIVATE-NEXT" }), 5);
  const results = await waiting;
  assert.deepEqual(results.map((result) => result.result.status), ["matched", "matched", "unmet"]);
  assert.equal(JSON.stringify(results).includes("PRIVATE-"), false);
  assert.equal(f.mutations.length, 0);
});

test("baseline expiry during native reads, unknown URLs, closure and deadlines remain explicit", async () => {
  let now = 0;
  const f = browser({ dateApi: { now: () => now } }), url_baseline_id = await urlBaseline(f), get = f.chrome.tabs.get;
  f.chrome.tabs.get = async (id) => { const tab = await get(id); now = 120000; return tab; };
  assert.equal((await f.call("browser.navigation.wait", { tab_id: 1, condition: "url_changed", url_baseline_id }, 200000)).error.code, "url_baseline_unavailable");
  f.chrome.tabs.get = get;
  const fresh = await urlBaseline(f);
  delete f.tabs[0].url;
  assert.equal((await f.status()).result.url_baseline_id, null);
  assert.equal((await f.wait({ condition: "url_changed", url_baseline_id: fresh, timeout_ms: 0 })).result.status, "unmet");
  f.close(1);
  assert.equal((await f.status()).result.url_baseline_id, null);
  assert.equal((await f.wait({ condition: "url_changed", url_baseline_id: fresh })).result.reason, "tab_closed");
  assert.equal((await f.call("browser.navigation.wait", { tab_id: 1, condition: "url_changed", url_baseline_id: fresh }, now)).error.code, "request_expired");
});

test("URL baseline and exact baseline arguments are strict and mutually exclusive", async () => {
  const f = browser(), url_baseline_id = await urlBaseline(f);
  for (const args of [
    {}, { url_baseline_id: null }, { url_baseline_id: 1 }, { url_baseline_id: "PRIVATE-URL" },
    { url_baseline_id, baseline_url: f.tabs[0].url }, { url_baseline_id, baseline_url: null },
    { url_baseline_id, url: f.tabs[0].url }, { url_baseline_id, navigation_id: "nav_00000000-0000-0000-0000-000000000001" },
  ]) assert.equal((await f.wait({ condition: "url_changed", ...args, timeout_ms: 0 })).error.code, "invalid_arguments");
  assert.equal((await f.wait({ condition: "url", url: f.tabs[0].url, url_baseline_id })).error.code, "invalid_arguments");
  const baseline_url = privateURL; f.tabs[0].url = privateURL;
  assert.equal((await f.wait({ condition: "url_changed", baseline_url, timeout_ms: 0 })).result.status, "unmet");
  f.emit(1, { url: "https://test.example/?token=PRIVATE-NEXT" });
  assert.equal((await f.wait({ condition: "url_changed", baseline_url, timeout_ms: 0 })).result.status, "matched");
  f.chrome.tabs.get = async () => { throw Error(privateURL); };
  const failed = await f.wait({ condition: "url_changed", url_baseline_id });
  assert.equal(failed.error.code, "browser_operation_failed");
  assert.equal(JSON.stringify(failed).includes("PRIVATE-"), false);
  assert.equal(f.mutations.length, 0);
});
