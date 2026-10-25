import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fixture } from "./operations-helper.mjs";

const schemas = JSON.parse(readFileSync(new URL("../capability-schemas.json", import.meta.url), "utf8"));
const tab = { tab_id: 1 };
const clone = (value) => JSON.parse(JSON.stringify(value));

function browser(options = {}) {
  const tabs = [1, 2, 3].map((id) => ({ id, windowId: id === 3 ? 8 : 7, index: id === 2 ? 1 : 0,
    active: id !== 1, pinned: false, title: "token=PRIVATE-TITLE", url: "https://test.example/?token=PRIVATE-URL#PRIVATE-HASH" }));
  const f = fixture({ tabs, ...options });
  const windows = [{ id: 7, type: "normal", focused: false }, { id: 8, type: "normal", focused: true },
    { id: 9, type: "popup", focused: false }];
  const mutations = [];
  const snapshot = (id) => {
    const window = windows.find((item) => item.id === id);
    if (!window) throw Error(`No window with id: ${id}.`);
    return { ...window, tabs: tabs.filter((item) => item.windowId === id).map((item) => ({ ...item })) };
  };
  const getTab = (id) => { const found = tabs.find((item) => item.id === id); if (!found) throw Error("No tab with id"); return found; };
  f.chrome.tabs.update = async (id, args) => {
    mutations.push(["tabs.update", id, clone(args)]);
    Object.assign(getTab(id), args);
    return { ...getTab(id) };
  };
  f.chrome.tabs.remove = async (id) => { mutations.push(["tabs.remove", id]); tabs.splice(tabs.indexOf(getTab(id)), 1); };
  f.chrome.tabs.move = async (id, args) => {
    mutations.push(["tabs.move", id, clone(args)]);
    const selected = getTab(id), destination = args.windowId ?? selected.windowId;
    const sourceWindow = windows.find((item) => item.id === selected.windowId);
    const destinationWindow = windows.find((item) => item.id === destination);
    if (sourceWindow.editable === false) throw Error("Tabs cannot be edited right now (user may be dragging a tab).");
    if (sourceWindow.profile !== destinationWindow.profile) throw Error("Tabs can only be moved within the same profile.");
    const destinationTabs = tabs.filter((item) => item.id !== id && item.windowId === destination).sort((a, b) => a.index - b.index);
    const maxIndex = destinationTabs.length;
    let index = args.index === -1 ? maxIndex : Math.min(args.index, maxIndex);
    // A small model of Chrome constraints, not a browser implementation:
    // cross-window insertion cannot split a group; pinning constrains position.
    if (destination !== selected.windowId && destinationTabs[index - 1]?.groupId >= 0
      && destinationTabs[index - 1].groupId === destinationTabs[index]?.groupId) {
      throw Error("Tab operation is invalid as the specified input would disrupt group continuity in the tab strip.");
    }
    const pinnedCount = destinationTabs.filter((item) => item.pinned).length;
    index = selected.pinned ? Math.min(index, pinnedCount) : Math.max(index, pinnedCount);
    selected.windowId = destination;
    selected.index = index;
    return { ...selected };
  };
  for (const operation of ["reload", "goBack", "goForward"]) {
    f.chrome.tabs[operation] = async (id) => { getTab(id); mutations.push([`tabs.${operation}`, id]); };
  }
  f.chrome.windows = {
    get: async (id) => snapshot(id),
    getAll: async (args) => { assert.equal(args.populate, true); return windows.map((item) => snapshot(item.id)); },
    create: async (args) => {
      mutations.push(["windows.create", clone(args)]);
      windows.push({ id: 10, type: args.type, focused: true });
      tabs.push({ id: 10, windowId: 10, index: 0, active: true, pinned: false, url: args.url ?? "chrome://newtab/", title: "New" });
      return snapshot(10);
    },
    update: async (id, args) => { mutations.push(["windows.update", id, clone(args)]); Object.assign(windows.find((item) => item.id === id), args); return snapshot(id); },
    remove: async (id) => { mutations.push(["windows.remove", id]); windows.splice(windows.findIndex((item) => item.id === id), 1); },
  };
  return { ...f, windows, mutations, snapshot };
}

function manyTabs(count, options = {}) {
  const f = browser(options);
  f.tabs.splice(0, f.tabs.length, ...Array.from({ length: count }, (_, index) => ({ id: index + 1,
    windowId: 7, index, active: index === 0, pinned: false,
    title: `Tab ${index} token=PRIVATE-TITLE`, url: `https://test.example/${index}?token=PRIVATE-URL` })));
  return f;
}

async function success(f, name, args, deadline) {
  const response = await f.call(name, args, deadline);
  assert.equal(response.ok, true, JSON.stringify(response));
  assert.deepEqual(Object.keys(response.result).sort(), Object.keys(schemas.capabilities.find((entry) => entry.name === name).result_contract).sort());
  assert.equal(JSON.stringify(response).includes("PRIVATE-"), false);
  return response.result;
}

for (const [action, extra] of [["activate", {}], ["close", {}], ["set_pinned", { pinned: true }],
  ["set_pinned", { pinned: false }], ["move", { index: 0 }], ["move", { window_id: 8, index: -1 }],
  ["move", { window_id: 8, index: 500 }]]) {
  test(`tab ${action} ${JSON.stringify(extra)} executes once and reports safe observed state`, async () => {
    const f = browser();
    if (action === "set_pinned") f.tabs[0].pinned = !extra.pinned;
    const result = await success(f, "browser.tabs.manage", { action, tab, ...extra });
    assert.equal(result.tab.tab_id, 1);
    assert.equal(f.mutations.length, 1);
    if (action === "activate") assert.equal(result.tab.active, true);
    if (action === "set_pinned") assert.equal(result.tab.pinned, extra.pinned);
    if (action === "close") assert.equal(f.tabs.some((item) => item.id === 1), false);
    if (action === "move") {
      assert.equal(result.tab.window_id, extra.window_id ?? 7);
      assert.equal(result.tab.index, extra.index === 0 ? 0 : 1);
    }
  });
}

for (const [action, extra] of [["activate", {}], ["set_pinned", { pinned: true }], ["move", { index: 0, window_id: 8 }]]) {
  test(`tab ${action} verifies the actual resulting state without replay`, async () => {
    const f = browser();
    if (action === "move") f.chrome.tabs.move = async () => { f.mutations.push(["noop"]); return { index: 0 }; };
    else f.chrome.tabs.update = async () => { f.mutations.push(["noop"]); };
    assert.equal((await f.call("browser.tabs.manage", { action, tab, ...extra })).error.code, "state_not_reached");
    assert.equal(f.mutations.length, 1);
  });
}

test("window listing filters normal windows, bounds tab summaries and preserves complete close IDs", async () => {
  const f = browser();
  for (let id = 100; id < 160; id++) f.tabs.push({ ...f.tabs[0], id });
  const result = await success(f, "browser.windows.list", { limit: 1 });
  assert.equal(result.total, 2);
  assert.equal(result.truncated, true);
  const window = result.windows[0];
  assert.equal(window.tabs.length, 50);
  assert.equal(window.tabs_truncated, true);
  assert.equal(window.tab_ids.length, 62);
  assert.equal(window.total_tabs, 62);
  assert.deepEqual(clone((await success(f, "browser.windows.list", { window_id: 8 })).windows[0].tab_ids), [3]);
  f.windows.splice(0);
  assert.equal((await success(f, "browser.windows.list", {})).total, 0);
});

for (const count of [70, 123]) test(`all ${count} tab summaries paginate within one ordered snapshot`, async () => {
  const f = manyTabs(count), all = [];
  let args = { window_id: 7 }, snapshot;
  for (;;) {
    const result = await success(f, "browser.windows.list", args);
    assert.equal(result.total, 1);
    const page = result.windows[0];
    snapshot ??= page.tab_snapshot;
    assert.match(snapshot, /^[0-9a-f]{64}$/);
    assert.equal(page.tab_snapshot, snapshot);
    assert.equal(page.total_tabs, count);
    assert.equal(page.tab_ids.length, count);
    assert.equal(page.tab_offset, all.length);
    assert.equal(page.tab_limit, 50);
    assert.equal(page.tabs.length, Math.min(50, count - all.length));
    all.push(...page.tabs.map((item) => item.tab_id));
    if (!page.has_more) { assert.equal(page.next_offset, null); break; }
    assert.equal(page.next_offset, all.length);
    args = { window_id: 7, tab_offset: page.next_offset, tab_snapshot: snapshot };
  }
  assert.deepEqual(all, Array.from({ length: count }, (_, index) => index + 1));
  assert.equal(f.mutations.length, 0);
  const empty = (await success(f, "browser.windows.list", { window_id: 7, tab_offset: count, tab_snapshot: snapshot })).windows[0];
  assert.equal(empty.tabs.length, 0);
  assert.equal(empty.has_more, false);
  assert.equal(empty.next_offset, null);
  assert.equal((await f.call("browser.windows.list", { window_id: 7, tab_offset: count + 1, tab_snapshot: snapshot })).error.code, "invalid_arguments");
});

test("window limit remains independent of tab limit; exact-window continuation uses the returned offset", async () => {
  const f = manyTabs(110);
  const first = await success(f, "browser.windows.list", { limit: 1, tab_limit: 7 });
  assert.equal(first.total, 2);
  assert.equal(first.truncated, true);
  const window = first.windows[0];
  assert.equal(window.tabs.length, 7);
  assert.equal(window.tab_ids.length, 110);
  assert.equal(window.next_offset, 7);
  const next = (await success(f, "browser.windows.list", { window_id: 7, limit: 1, tab_limit: 7,
    tab_offset: window.next_offset, tab_snapshot: window.tab_snapshot })).windows[0];
  assert.deepEqual(clone(next.tabs.map((item) => item.index)), [7, 8, 9, 10, 11, 12, 13]);
  const empty = (await success(f, "browser.windows.list", { window_id: 8 })).windows[0];
  assert.equal(empty.total_tabs, 0);
  assert.equal(empty.has_more, false);
  assert.equal(empty.next_offset, null);
});

for (const change of ["add", "remove", "replace", "reorder", "move_out"]) {
  test(`pagination rejects ${change} instead of mixing window states`, async () => {
    const f = manyTabs(70);
    const first = (await f.call("browser.windows.list", { window_id: 7 })).result.windows[0];
    if (change === "add") f.tabs.push({ ...f.tabs[0], id: 71, index: 70 });
    if (change === "remove") f.tabs.pop();
    if (change === "replace") f.tabs[65].id = 999;
    if (change === "reorder") {
      [f.tabs[60], f.tabs[61]] = [f.tabs[61], f.tabs[60]];
      [f.tabs[60].index, f.tabs[61].index] = [60, 61];
    }
    if (change === "move_out") f.tabs[65].windowId = 8;
    const result = await f.call("browser.windows.list", { window_id: 7, tab_offset: first.next_offset, tab_snapshot: first.tab_snapshot });
    assert.equal(result.error.code, "window_changed");
    assert.equal(result.result, undefined);
    const fresh = (await f.call("browser.windows.list", { window_id: 7 })).result.windows[0];
    assert.notEqual(fresh.tab_snapshot, first.tab_snapshot);
    assert.equal(f.mutations.length, 0);
  });
}

test("snapshot binds window identity but does not freeze display metadata or close order", async () => {
  const f = manyTabs(70);
  const first = (await f.call("browser.windows.list", { window_id: 7 })).result.windows[0];
  assert.equal((await f.call("browser.windows.list", { window_id: 8, tab_snapshot: first.tab_snapshot })).error.code, "window_changed");
  f.tabs[60].title = "Updated token=PRIVATE-TITLE";
  const next = (await success(f, "browser.windows.list", { window_id: 7, tab_offset: 50, tab_snapshot: first.tab_snapshot })).windows[0];
  assert.equal(next.tab_snapshot, first.tab_snapshot);
  assert.match(next.tabs[10].title, /Updated/);
  f.tabs.reverse();
  assert.equal((await f.call("browser.windows.list", { window_id: 7, tab_offset: 50, tab_snapshot: first.tab_snapshot })).error.code, "window_changed");
  // Close still compares complete membership, not snapshot/order or one page.
  await success(f, "browser.windows.manage", { action: "close", window_id: 7, expected_tab_ids: first.tab_ids });
  assert.deepEqual(f.mutations, [["windows.remove", 7]]);
});

test("pagination retains byte/deadline bounds without partial close IDs", async () => {
  let now = 0;
  const f = manyTabs(70, { dateApi: { now: () => now }, cryptoApi: { subtle: {
    digest: async (...args) => { const digest = await crypto.subtle.digest(...args); now = 1000; return digest; },
  } } });
  assert.equal((await f.call("browser.windows.list", { window_id: 7 }, 1000)).error.code, "request_expired");
  const large = manyTabs(70);
  for (const item of large.tabs) { item.title = "标".repeat(200); item.url = "https://test.example/" + "长".repeat(100); }
  assert.equal((await large.call("browser.windows.list", { window_id: 7 })).error.code, "result_too_large");
  const smaller = (await success(large, "browser.windows.list", { window_id: 7, tab_limit: 1 })).windows[0];
  assert.equal(smaller.tab_ids.length, 70);
  assert.equal(smaller.tabs.length, 1);
  assert.equal(smaller.next_offset, 1);
});

test("oversized windows refuse partial close preconditions", async () => {
  const f = browser();
  for (let id = 100; id < 10100; id++) f.tabs.push({ ...f.tabs[0], id });
  assert.equal((await f.call("browser.windows.list", {})).error.code, "result_too_large");
  const other = browser();
  for (let id = 1000000; id < 1009000; id++) other.tabs.push({ ...other.tabs[0], id });
  assert.equal((await other.call("browser.windows.list", {})).error.code, "result_too_large");
  assert.equal(f.mutations.length, 0);
});

for (const url of [undefined, "https://test.example/?token=PRIVATE-URL"]) test(`create normal window ${url === undefined ? "default" : "URL"}`, async () => {
  const f = browser();
  const result = await success(f, "browser.windows.manage", { action: "create", ...(url ? { url } : {}) });
  assert.equal(result.window.window_id, 10);
  assert.equal(result.window.type, "normal");
  assert.equal(f.mutations.length, 1);
});

test("focus window verifies focused state", async () => {
  const f = browser();
  assert.equal((await success(f, "browser.windows.manage", { action: "focus", window_id: 7 })).window.focused, true);
  f.windows[0].focused = false;
  f.chrome.windows.update = async () => { f.mutations.push(["noop"]); };
  assert.equal((await f.call("browser.windows.manage", { action: "focus", window_id: 7 })).error.code, "state_not_reached");
  assert.equal(f.mutations.length, 2);
});

test("close compares complete tab membership ignoring order, including tabs beyond the summary limit", async () => {
  const f = browser();
  const args = { action: "close", window_id: 7, expected_tab_ids: [2, 1] };
  await success(f, "browser.windows.manage", args);
  assert.deepEqual(f.mutations, [["windows.remove", 7]]);
  for (const change of ["add", "remove", "replace", "move"]) {
    const changed = browser();
    const observed = (await changed.call("browser.windows.list", { window_id: 7 })).result.windows[0];
    if (change === "add") changed.tabs.push({ ...changed.tabs[0], id: 4 });
    if (change === "remove") changed.tabs.splice(1, 1);
    if (change === "replace") changed.tabs[0].id = 4;
    if (change === "move") changed.tabs[0].windowId = 8;
    assert.equal((await changed.call("browser.windows.manage", { ...args, expected_tab_ids: observed.tab_ids })).error.code, "window_changed", change);
    assert.equal(changed.mutations.length, 0);
  }
  const many = browser();
  for (let id = 100; id < 160; id++) many.tabs.push({ ...many.tabs[0], id });
  const observed = (await many.call("browser.windows.list", { window_id: 7 })).result.windows[0];
  many.tabs.at(-1).id = 999;
  assert.equal((await many.call("browser.windows.manage", { ...args, expected_tab_ids: observed.tab_ids })).error.code, "window_changed");
  assert.equal(many.mutations.length, 0);
});

test("exact window IDs and normal window types are required", async () => {
  const f = browser();
  for (const id of [999, 9]) {
    const expected = id === 999 ? "window_not_found" : "unsupported_window";
    for (const [name, args] of [["browser.windows.list", { window_id: id }],
      ["browser.windows.manage", { action: "focus", window_id: id }],
      ["browser.windows.manage", { action: "close", window_id: id, expected_tab_ids: [] }],
      ["browser.tabs.manage", { action: "move", tab, window_id: id, index: 0 }]]) {
      assert.equal((await f.call(name, args)).error.code, expected);
    }
  }
  f.tabs[0].windowId = 9;
  assert.equal((await f.call("browser.tabs.manage", { action: "move", tab, index: 0 })).error.code, "unsupported_window");
  for (const type of ["app", "devtools"]) {
    f.windows[2].type = type;
    assert.equal((await f.call("browser.windows.manage", { action: "close", window_id: 9, expected_tab_ids: [1] })).error.code, "unsupported_window");
  }
  assert.equal(f.mutations.length, 0);
});

test("window query exceptions are sanitized; post-action observation failures never retry mutations", async () => {
  for (const query of ["get", "getAll"]) {
    const f = browser();
    f.chrome.windows[query] = async () => { throw Error("PRIVATE-EXCEPTION"); };
    const result = await f.call("browser.windows.list", query === "get" ? { window_id: 7 } : {});
    assert.equal(result.error.code, "browser_operation_failed");
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
    assert.equal(f.mutations.length, 0);
  }
  const f = browser();
  const get = f.chrome.tabs.get;
  f.chrome.tabs.get = async (id) => { if (f.mutations.length) throw Error("PRIVATE-EXCEPTION"); return get(id); };
  const result = await f.call("browser.tabs.manage", { action: "activate", tab });
  assert.equal(result.error.code, "browser_operation_failed");
  assert.equal(f.mutations.length, 1);
});

for (const name of ["browser.tabs.manage", "browser.navigation.go"]) test(`${name} rejects absent, ambiguous and conflicting selectors`, async () => {
  const f = browser();
  for (const [selector, expected] of [[{ tab_id: 999 }, "tab_not_found"], [{ title: f.tabs[0].title }, "ambiguous_tab"],
    [{ tab_id: 1, title: "Wrong" }, "tab_not_found"]]) {
    const response = await f.call(name, { action: name.endsWith("manage") ? "close" : "reload", tab: selector });
    assert.equal(response.error.code, expected);
    assert.equal(JSON.stringify(response).includes("PRIVATE-"), false);
  }
  assert.equal(f.mutations.length, 0);
});

test("selector is checked again after asynchronous move window checks", async () => {
  const f = browser();
  f.chrome.windows.get = async (id) => { f.tabs[0].title = "Changed"; return f.snapshot(id); };
  assert.equal((await f.call("browser.tabs.manage", { action: "move", tab: { tab_id: 1, title: "token=PRIVATE-TITLE" }, index: 0 })).error.code, "page_changed");
  assert.equal(f.mutations.length, 0);
});

for (const action of ["navigate", "reload", "back", "forward"]) test(`navigation ${action} initiates exactly once without waiting or page injection`, async () => {
  const f = browser({ consent: [], granted: false });
  const args = { action, tab, ...(action === "navigate" ? { url: "https://new.example/?token=PRIVATE-URL" } : {}) };
  const result = await success(f, "browser.navigation.go", args);
  assert.equal(result.status, "initiated");
  assert.equal(result.navigation_waited, false);
  assert.equal(result.tab_id, 1);
  assert.equal(f.mutations.length, 1);
  assert.equal(f.calls.injected.length, 0);
  assert.equal(f.calls.messages.length, 0);
});

for (const action of ["navigate", "reload", "back", "forward"]) {
  for (const [label, before, after] of [
    ["continuous loading", { status: "loading" }, { status: "loading" }],
    ["stable pending URL", { status: "loading", pendingUrl: "https://pending.example/" },
      { status: "loading", pendingUrl: "https://pending.example/" }],
    ["loading to complete", { status: "loading" }, { status: "complete" }],
    ["complete to loading", { status: "complete" }, { status: "loading" }],
    ["status change with stable pending URL", { status: "complete", pendingUrl: "https://pending.example/" },
      { status: "loading", pendingUrl: "https://pending.example/" }],
  ]) {
    test(`navigation ${action} allows ${label} with a stable target and dispatches once`, async () => {
      const f = browser(), get = f.chrome.tabs.get;
      Object.assign(f.tabs[0], before);
      const selector = { ...tab, url: f.tabs[0].url, title: f.tabs[0].title };
      let reads = 0;
      f.chrome.tabs.get = async (id) => {
        if (++reads === 2) Object.assign(f.tabs[0], after);
        return get(id);
      };
      const result = await success(f, "browser.navigation.go", { action, tab: selector,
        ...(action === "navigate" ? { url: "https://destination.example/" } : {}) });
      assert.equal(result.status, "initiated");
      assert.equal(result.navigation_waited, false);
      assert.equal(reads, 2); // One resolve and one recheck, no waiting/polling.
      const method = { navigate: "update", reload: "reload", back: "goBack", forward: "goForward" }[action];
      assert.deepEqual(f.mutations, [[`tabs.${method}`, 1, ...(action === "navigate" ? [{ url: "https://destination.example/" }] : [])]]);
      assert.equal(f.calls.injected.length, 0);
      assert.equal(f.calls.messages.length, 0);
    });
  }
  for (const change of ["url", "title", "disappeared", "identity", "pending_added", "pending_changed", "pending_removed", "implicit_url"]) {
    test(`navigation ${action} refuses ${change} between resolution and dispatch`, async () => {
      const f = browser(), get = f.chrome.tabs.get;
      const selector = change === "implicit_url" ? tab : { ...tab, url: f.tabs[0].url, title: f.tabs[0].title };
      f.tabs[0].status = "loading";
      if (["pending_changed", "pending_removed"].includes(change)) f.tabs[0].pendingUrl = "https://pending.example/";
      let reads = 0;
      f.chrome.tabs.get = async (id) => {
        if (++reads === 2) {
          if (["url", "implicit_url"].includes(change)) f.tabs[0].url = "https://changed.example/";
          if (change === "title") f.tabs[0].title = "Changed";
          if (change === "disappeared") f.tabs.splice(0, 1);
          if (change === "pending_added") f.tabs[0].pendingUrl = "https://pending.example/";
          if (change === "pending_changed") f.tabs[0].pendingUrl = "https://other-pending.example/";
          if (change === "pending_removed") { delete f.tabs[0].pendingUrl; f.tabs[0].status = "complete"; }
          if (change === "identity") return { ...f.tabs[0], id: 999 };
        }
        return get(id);
      };
      const result = await f.call("browser.navigation.go", { action, tab: selector,
        ...(action === "navigate" ? { url: "https://destination.example/" } : {}) });
      assert.equal(result.error.code, "page_changed");
      assert.equal(reads, 2);
      assert.equal(f.mutations.length, 0);
      assert.equal(f.calls.injected.length, 0);
    });
  }
}

test("navigation recheck errors are sanitized without waiting or retrying", async () => {
  const f = browser(), get = f.chrome.tabs.get;
  let reads = 0;
  f.chrome.tabs.get = async (id) => { if (++reads === 2) throw Error("PRIVATE-EXCEPTION"); return get(id); };
  const result = await f.call("browser.navigation.go", { action: "reload", tab });
  assert.equal(result.error.code, "browser_operation_failed");
  assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
  assert.equal(reads, 2);
  assert.equal(f.mutations.length, 0);
});

test("navigation checks expiry after its final target read", async () => {
  let now = 0, reads = 0;
  const f = browser({ dateApi: { now: () => now } }), get = f.chrome.tabs.get;
  f.chrome.tabs.get = async (id) => { if (++reads === 2) now = 1000; return get(id); };
  assert.equal((await f.call("browser.navigation.go", { action: "reload", tab }, 1000)).error.code, "request_expired");
  assert.equal(f.mutations.length, 0);
});

for (const [pinned, index, expected] of [[true, -1, 1], [false, 0, 2]]) {
  test(`Chrome move respects ${pinned ? "pinned" : "unpinned"} boundary and reports actual index`, async () => {
    const f = manyTabs(5);
    f.tabs[0].pinned = true;
    f.tabs[1].pinned = true;
    const selected = pinned ? 1 : 3;
    const result = await success(f, "browser.tabs.manage", { action: "move", tab: { tab_id: selected }, index });
    assert.equal(result.tab.index, expected);
    assert.equal(result.tab.pinned, pinned);
    assert.equal(f.mutations.length, 1);
    assert.equal(f.mutations[0][2].index, index); // Agent forwards, Chrome constrains.
    assert.equal(f.chrome.tabs.group, undefined);
    assert.equal(f.chrome.tabs.ungroup, undefined);
  });
}

test("cross-window insertion that would split a group is refused once without fallback", async () => {
  const f = browser();
  f.tabs[2].groupId = 4;
  f.tabs.push({ ...f.tabs[2], id: 4, index: 1 });
  const before = clone(f.tabs);
  const result = await f.call("browser.tabs.manage", { action: "move", tab, window_id: 8, index: 1 });
  assert.equal(result.error.code, "browser_operation_failed");
  assert.equal(JSON.stringify(result).includes("group continuity"), false);
  assert.deepEqual(clone(f.tabs), before);
  assert.equal(f.mutations.length, 1);
  assert.equal(f.mutations[0][2].index, 1);
});

test("Chrome can reject a cross-window move or busy tab strip without a second attempt", async () => {
  for (const reason of ["profile", "dragging"]) {
    const f = browser();
    if (reason === "profile") f.windows[1].profile = "other";
    else f.windows[0].editable = false;
    const before = clone(f.tabs);
    const result = await f.call("browser.tabs.manage", { action: "move", tab, window_id: 8, index: 0 });
    assert.equal(result.error.code, "browser_operation_failed");
    assert.deepEqual(clone(f.tabs), before);
    assert.equal(f.mutations.length, 1);
  }
  const f = browser();
  for (const index of [-2, 2 ** 31, 1.5]) {
    assert.equal((await f.call("browser.tabs.manage", { action: "move", tab, index })).error.code, "invalid_arguments");
  }
  assert.equal(f.mutations.length, 0);
});

for (const change of ["index", "window", "missing", "read_error"]) {
  test(`successful Chrome move followed by ${change} observation failure is never replayed`, async () => {
    const f = browser(), get = f.chrome.tabs.get;
    f.chrome.tabs.get = async (id) => {
      if (f.mutations.length) {
        if (change === "missing") f.tabs.splice(f.tabs.findIndex((item) => item.id === id), 1);
        if (change === "read_error") throw Error("PRIVATE-EXCEPTION");
        const result = await get(id);
        return { ...result, ...(change === "index" ? { index: result.index + 1 } : { windowId: 7 }) };
      }
      return get(id);
    };
    const result = await f.call("browser.tabs.manage", { action: "move", tab, window_id: 8, index: -1 });
    assert.equal(result.error.code, ["missing", "read_error"].includes(change) ? "browser_operation_failed" : "state_not_reached");
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
    assert.equal(f.mutations.length, 1);
  });
}

for (const [action, method] of [["back", "goBack"], ["forward", "goForward"]]) test(`${action} unavailable history has a safe error without fallback`, async () => {
  const f = browser();
  let calls = 0;
  f.chrome.tabs[method] = async () => { calls++; throw Error("Cannot find a next page in history."); };
  assert.equal((await f.call("browser.navigation.go", { action, tab })).error.code, "history_unavailable");
  assert.equal(calls, 1);
  assert.equal(f.mutations.length, 0);
});

const sideEffects = [
  ["browser.tabs.manage", { action: "activate", tab }, "tabs", "update"],
  ["browser.tabs.manage", { action: "set_pinned", tab, pinned: true }, "tabs", "update"],
  ["browser.tabs.manage", { action: "close", tab }, "tabs", "remove"],
  ["browser.tabs.manage", { action: "move", tab, index: 0 }, "tabs", "move"],
  ["browser.windows.manage", { action: "create" }, "windows", "create"],
  ["browser.windows.manage", { action: "focus", window_id: 7 }, "windows", "update"],
  ["browser.windows.manage", { action: "close", window_id: 7, expected_tab_ids: [1, 2] }, "windows", "remove"],
  ...["navigate", "reload", "back", "forward"].map((action, index) => ["browser.navigation.go",
    { action, tab, ...(action === "navigate" ? { url: "https://new.example/" } : {}) }, "tabs", ["update", "reload", "goBack", "goForward"][index]]),
];

for (const [name, args, area, method] of sideEffects) {
  test(`${name} ${args.action}: Chrome exception is sanitized and never retried`, async () => {
    const f = browser();
    let attempts = 0;
    f.chrome[area][method] = async () => { attempts++; throw Error("PRIVATE-EXCEPTION"); };
    const result = await f.call(name, args);
    assert.equal(result.error.code, "browser_operation_failed");
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
    assert.equal(attempts, 1);
  });
  test(`${name} ${args.action}: expiry during preparation prevents mutation`, async () => {
    let now = 0;
    const f = browser({ dateApi: { now: () => now } });
    const originalTabGet = f.chrome.tabs.get, originalWindowGet = f.chrome.windows.get;
    f.chrome.tabs.get = async (id) => { now = 1000; return originalTabGet(id); };
    f.chrome.windows.get = async (id) => { now = 1000; return originalWindowGet(id); };
    // create has no asynchronous preparation, so test the entry deadline instead.
    if (args.action === "create") now = 1000;
    assert.equal((await f.call(name, args, 1000)).error.code, "request_expired");
    assert.equal(f.mutations.length, 0);
  });
  test(`${name} ${args.action}: expiry after dispatch never replays the mutation`, async () => {
    let now = 0;
    const f = browser({ dateApi: { now: () => now } });
    const original = f.chrome[area][method];
    f.chrome[area][method] = async (...values) => { const result = await original(...values); now = 1000; return result; };
    assert.equal((await f.call(name, args, 1000)).error.code, "request_expired");
    assert.equal(f.mutations.length, 1);
  });
}

test("action validation rejects missing, unrelated and mistyped parameters before Chrome APIs", async () => {
  const f = browser();
  const invalid = [
    ["browser.tabs.manage", { action: "move", tab }],
    ["browser.tabs.manage", { action: "set_pinned", tab }],
    ["browser.windows.manage", { action: "close", window_id: 7 }],
    ["browser.windows.manage", { action: "close", window_id: 7, expected_tab_ids: [1, 1] }],
    ["browser.navigation.go", { action: "navigate", tab }],
    ["browser.navigation.go", { action: "reload", tab, url: null }],
  ];
  for (const [name, args] of sideEffects) {
    invalid.push([name, { ...args, action: "unknown" }], [name, { ...args, unexpected: true }]);
    for (const [field, value] of Object.entries(args)) {
      for (const probe of [null, [], {}, "", 1.5, true]) {
        if (field === "pinned" && probe === true) continue;
        if (field === "expected_tab_ids" && Array.isArray(probe)) continue;
        invalid.push([name, { ...args, [field]: probe }]);
      }
    }
  }
  for (const url of ["file:///a", "chrome://settings", "javascript:alert(1)", "https://user:pass@test.example/", "https://test.example:0/", "https://test.example/\u200b", "https://test.example/a b"]) {
    invalid.push(["browser.navigation.go", { action: "navigate", tab, url }], ["browser.windows.manage", { action: "create", url }]);
  }
  for (const [name, args] of invalid) assert.equal((await f.call(name, args)).error.code, "invalid_arguments", `${name} ${JSON.stringify(args)}`);
  assert.equal(f.mutations.length, 0);
});

test("tab pagination rejects invalid fields and unbound continuation before querying Chrome", async () => {
  const f = browser();
  let queries = 0;
  f.chrome.windows.get = f.chrome.windows.getAll = async () => { queries++; throw Error("Must not query"); };
  for (const args of [
    { tab_offset: 1 }, { window_id: 7, tab_offset: 50 }, { tab_snapshot: "a".repeat(64) },
    { window_id: 7, tab_offset: 50, tab_snapshot: null },
    ...[-1, 10001, true, "0", null].map((tab_offset) => ({ window_id: 7, tab_offset })),
    ...[0, 51, 1.5, true, "1", null].map((tab_limit) => ({ tab_limit })),
    ...["", "A".repeat(64), "a".repeat(63), "a".repeat(65), 1, false].map((tab_snapshot) => ({ window_id: 7, tab_snapshot })),
    { window_id: 7, tab_offset: 50, tab_snapshot: "a".repeat(64), unexpected: true },
  ]) assert.equal((await f.call("browser.windows.list", args)).error.code, "invalid_arguments", JSON.stringify(args));
  assert.equal(queries, 0);
});
