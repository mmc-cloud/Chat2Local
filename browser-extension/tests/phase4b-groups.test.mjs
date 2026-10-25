import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { browser } from "./phase4b-helper.mjs";

async function create(f, extra = {}) {
  const result = await f.manage({ action: "create", tab_ids: [1, 2], ...extra });
  assert.equal(result.ok, true, JSON.stringify(result));
  return result.result.group.group_id;
}

test("create/update/move/add/remove use native IDs and freshly observed state", async () => {
  const f = browser();
  const id = await create(f, { title: "token=PRIVATE-TITLE", color: "blue", collapsed: true });
  assert.equal(f.mutations.length, 2);
  const update = await f.manage({ action: "update", group_id: id, title: "", color: "red", collapsed: false });
  assert.equal(update.result.group.title, "");
  assert.equal(update.result.group.color, "red");
  assert.equal(update.result.group.collapsed, false);
  const moved = await f.manage({ action: "move", group_id: id, index: -1, window_id: 8 });
  assert.equal(moved.result.group.window_id, 8);
  assert.deepEqual(Array.from(moved.result.group.tab_ids), [1, 2]);
  assert.equal((await f.manage({ action: "add_tabs", group_id: id, tab_ids: [3] })).ok, true);
  const removed = await f.manage({ action: "remove_tabs", group_id: id, tab_ids: [1, 2, 3] });
  assert.equal(removed.ok, true);
  assert.equal(removed.result.group, null);
  assert.equal(f.groups.length, 0);
  assert.equal(f.chrome.tabs.update.name === "unpin", false);
});

test("all group capabilities require both user consent and actual optional permission", async () => {
  for (const [granted, consent] of [[false, true], [true, false], [false, false]]) {
    const f = browser({ granted, consent });
    for (const result of [await f.list(), await f.manage({ action: "create", tab_ids: [1] })]) {
      assert.equal(result.error.code, "groups_permission_denied");
    }
    assert.equal(f.mutations.length, 0);
  }
  const f = browser(); const id = await create(f);
  f.permissions.granted = false;
  assert.equal((await f.list()).error.code, "groups_permission_denied");
  assert.equal((await f.manage({ action: "update", group_id: id, title: "New" })).error.code, "groups_permission_denied");
  assert.equal(f.mutations.length, 1);
});

test("exact IDs distinguish same-title groups, group/window filters are ANDed", async () => {
  const f = browser();
  const a = await create(f, { tab_ids: [1], title: "Same" });
  const b = await create(f, { tab_ids: [3], title: "Same" });
  assert.equal((await f.list()).result.groups.length, 2);
  assert.equal((await f.list({ group_id: b })).result.groups[0].window_id, 8);
  assert.equal((await f.list({ group_id: b, window_id: 7 })).error.code, "group_not_found");
  await f.manage({ action: "update", group_id: a, title: "Changed" });
  assert.equal(f.groups.find((g) => g.id === b).title, "Same");
  assert.equal((await f.list({ group_id: 999 })).error.code, "group_not_found");
});

test("bounded member pages preserve full IDs and reject changed membership/order", async () => {
  const f = browser(); const id = await create(f);
  for (let index = 2; index < 123; index++) f.tabs.push({ ...f.tabs[0], id: index + 100, index });
  const first = (await f.list({ group_id: id })).result.groups[0];
  assert.equal(first.total_tabs, 123); assert.equal(first.tabs.length, 50); assert.equal(first.tab_ids.length, 123);
  const second = (await f.list({ group_id: id, tab_offset: 50, tab_snapshot: first.tab_snapshot })).result.groups[0];
  assert.equal(second.tabs.length, 50); assert.equal(second.next_offset, 100);
  const last = (await f.list({ group_id: id, tab_offset: 100, tab_snapshot: first.tab_snapshot })).result.groups[0];
  assert.equal(last.tabs.length, 23); assert.equal(last.next_offset, null);
  assert.equal(JSON.stringify(last).includes("PRIVATE-"), false);
  assert.equal((await f.list({ group_id: id, tab_offset: 124, tab_snapshot: first.tab_snapshot })).error.code, "invalid_arguments");
  f.tabs[0].index = 90;
  assert.equal((await f.list({ group_id: id, tab_offset: 50, tab_snapshot: first.tab_snapshot })).error.code, "group_changed");
});

test("group-list pagination detects added/moved groups and requires its snapshot", async () => {
  const f = browser(); await create(f, { tab_ids: [1] }); await create(f, { tab_ids: [3] });
  const first = (await f.list({ limit: 1 })).result;
  assert.equal(first.has_more, true);
  assert.equal((await f.list({ offset: first.next_offset, limit: 1, snapshot: first.snapshot })).result.groups.length, 1);
  f.groups[1].windowId = 7;
  assert.equal((await f.list({ offset: 1, snapshot: first.snapshot })).error.code, "group_changed");
  assert.equal((await f.list({ offset: 1 })).error.code, "invalid_arguments");
});

test("pinned/native/profile/move errors are sanitized and never retried", async () => {
  const f = browser(); f.tabs[0].pinned = true;
  const pinned = await f.manage({ action: "create", tab_ids: [1] });
  assert.equal(pinned.error.code, "browser_operation_failed");
  assert.equal(pinned.error.details.progress.outcome, "unknown");
  assert.equal(f.tabs[0].pinned, true); assert.equal(f.mutations.length, 1);
  assert.equal(JSON.stringify(pinned).includes("PRIVATE-"), false);
  f.tabs[0].pinned = false; const id = await create(f);
  let attempts = 0;
  f.chrome.tabGroups.move = async () => { attempts++; throw Error("PRIVATE-PROFILE-ERROR"); };
  const result = await f.manage({ action: "move", group_id: id, window_id: 8, index: 0 });
  assert.equal(result.error.code, "browser_operation_failed"); assert.equal(attempts, 1);
  assert.equal((await f.manage({ action: "move", group_id: id, window_id: 9, index: 0 })).error.code, "unsupported_window");
  assert.equal(attempts, 1);
});

test("multi-step create failure reports grouped ID and does not rollback or retry", async () => {
  const f = browser(); let updates = 0;
  f.chrome.tabGroups.update = async () => { updates++; throw Error("PRIVATE-FAILURE"); };
  const result = await f.manage({ action: "create", tab_ids: [1, 2], title: "New" });
  assert.equal(result.error.code, "browser_operation_failed");
  const progress = result.error.details.progress;
  assert.deepEqual(Array.from(progress.completed_steps), ["grouped"]);
  assert.equal(progress.group_id, f.groups[0].id);
  assert.equal(progress.outcome, "partial");
  assert.equal(f.tabs[0].groupId, progress.group_id);
  assert.equal(updates, 1); assert.equal(f.mutations.length, 1);
});

test("revocation after grouping prevents metadata update and reports partial completion", async () => {
  const f = browser(), group = f.chrome.tabs.group;
  f.chrome.tabs.group = async (args) => { const id = await group(args); f.settings.groupAccess = false; return id; };
  const result = await f.manage({ action: "create", tab_ids: [1], color: "blue" });
  assert.equal(result.error.code, "groups_permission_denied");
  assert.deepEqual(Array.from(result.error.details.progress.completed_steps), ["grouped"]);
  assert.equal(f.groups[0].color, "grey"); assert.equal(f.mutations.length, 1);
});

test("target changes and foreign group members are rejected before mutation", async () => {
  const f = browser(); const id = await create(f, { tab_ids: [1] });
  assert.equal((await f.manage({ action: "remove_tabs", group_id: id, tab_ids: [2] })).error.code, "group_changed");
  assert.equal((await f.manage({ action: "add_tabs", group_id: id, tab_ids: [999] })).error.code, "tab_not_found");
  const getWindow = f.chrome.windows.get;
  f.chrome.windows.get = async (windowId) => { f.tabs[1].pinned = true; return getWindow(windowId); };
  assert.equal((await f.manage({ action: "add_tabs", group_id: id, tab_ids: [2] })).error.code, "group_changed");
  assert.equal(f.mutations.length, 1);
});

test("successful API calls still require actual property/member verification", async () => {
  const f = browser(); const id = await create(f);
  let updates = 0;
  f.chrome.tabGroups.update = async () => { updates++; };
  const result = await f.manage({ action: "update", group_id: id, color: "orange" });
  assert.equal(result.error.code, "state_not_reached");
  assert.deepEqual(Array.from(result.error.details.progress.completed_steps), ["updated"]);
  assert.equal(updates, 1);
});

test("strict actions refuse irrelevant/null/type/duplicate arguments before permission or mutation", async () => {
  const f = browser();
  for (const args of [
    { action: "create", tab_ids: [] }, { action: "create", tab_ids: [1, 1] }, { action: "create", tab_ids: [true] },
    { action: "create", tab_ids: [1], group_id: null }, { action: "update", group_id: 1 },
    { action: "update", group_id: 1, title: null }, { action: "update", group_id: 1, collapsed: 1 },
    { action: "update", group_id: 1, color: "BLUE" }, { action: "move", group_id: 1, index: -2 },
    { action: "add_tabs", group_id: 1, tab_ids: [1], title: "x" }, { action: "remove_tabs", tab_ids: [1] },
    { action: "delete", group_id: 1 }, { action: "create", tab_ids: [1], title: "x".repeat(257) },
  ]) assert.equal((await f.manage(args)).error.code, "invalid_arguments", JSON.stringify(args));
  assert.equal(f.mutations.length, 0);
});

test("member changes during move preparation refuse the entire group mutation", async () => {
  const f = browser(); const id = await create(f);
  const getWindow = f.chrome.windows.get;
  f.chrome.windows.get = async (windowId) => { f.tabs[2].groupId = id; f.tabs[2].windowId = 7; return getWindow(windowId); };
  const result = await f.manage({ action: "move", group_id: id, index: -1 });
  assert.equal(result.error.code, "group_changed");
  assert.equal(f.mutations.length, 1);
});

test("deadline after successful grouping reports partial effects and skips the next API", async () => {
  let now = 0;
  const f = browser({ dateApi: { now: () => now } }), group = f.chrome.tabs.group;
  f.chrome.tabs.group = async (args) => { const id = await group(args); now = 1000; return id; };
  const result = await f.call("browser.groups.manage", { action: "create", tab_ids: [1], title: "New" }, 1000);
  assert.equal(result.error.code, "request_expired");
  assert.deepEqual(Array.from(result.error.details.progress.completed_steps), ["grouped"]);
  assert.equal(f.mutations.length, 1);
});

test("large group collections paginate and oversized member summaries keep the byte budget", async () => {
  const f = browser();
  f.tabs.splice(0);
  for (let index = 0; index < 51; index++) {
    f.groups.push({ id: index + 100, windowId: 7, title: "Same", color: "grey", collapsed: false });
    f.tabs.push({ id: index + 1, windowId: 7, groupId: index + 100, index, pinned: false, active: false,
      title: "token=PRIVATE-TITLE", url: "https://test.example/?token=PRIVATE-URL" });
  }
  const ids = [];
  let args = { limit: 20 };
  for (;;) {
    const result = await f.list(args); assert.equal(result.ok, true);
    assert.equal(result.result.total, 51); assert.ok(result.result.groups.length <= 20);
    ids.push(...result.result.groups.map((group) => group.group_id));
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
    if (!result.result.has_more) break;
    args = { limit: 20, offset: result.result.next_offset, snapshot: result.result.snapshot };
  }
  assert.equal(new Set(ids).size, 51);
  for (const tab of f.tabs) { tab.groupId = 100; tab.title = "标".repeat(200); tab.url = "https://test.example/" + "长".repeat(100); }
  f.groups.splice(1);
  assert.equal((await f.list()).error.code, "result_too_large");
  const small = await f.list({ group_id: 100, tab_limit: 1 });
  assert.equal(small.ok, true); assert.equal(small.result.groups[0].tab_ids.length, 51);
});

test("Phase 4B success fields match the published result contracts", async () => {
  const f = browser();
  const schema = JSON.parse(readFileSync(new URL("../capability-schemas.json", import.meta.url), "utf8"));
  const created = await f.manage({ action: "create", tab_ids: [1], title: "token=PRIVATE-TITLE" });
  const results = [
    ["browser.groups.manage", created], ["browser.groups.list", await f.list()],
    ["browser.navigation.status", await f.status()],
    ["browser.navigation.wait", await f.wait({ condition: "current_complete", url: f.tabs[0].url, timeout_ms: 0 })],
  ];
  for (const [name, response] of results) {
    assert.equal(response.ok, true, JSON.stringify(response));
    const contract = schema.capabilities.find((entry) => entry.name === name).result_contract;
    assert.deepEqual(Object.keys(response.result).sort(), Object.keys(contract).sort(), name);
    assert.equal(JSON.stringify(response).includes("PRIVATE-"), false);
  }
});

async function movableGroup(options) {
  const f = browser(options), id = await create(f);
  f.tabs[0].index = 1; f.tabs[1].index = 2;
  for (const [tabId, index] of [[10, 0], [11, 3], [12, 4]]) {
    f.tabs.push({ ...f.tabs[0], id: tabId, index, groupId: -1, active: false });
  }
  return { f, id };
}

test("group move verifies actual position for left/right, cross-window, end and existing slots", async () => {
  for (const [index, window_id, expected] of [[0, 7, 0], [3, 7, 3], [-1, 7, 3], [100, 7, 3],
    [1, 7, 1], [0, 8, 0], [100, 8, 1]]) {
    const { f, id } = await movableGroup();
    const result = await f.manage({ action: "move", group_id: id, index, window_id });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.result.actual_index, expected);
    assert.equal(result.result.group.window_id, window_id);
    assert.deepEqual(Array.from(result.result.group.tabs, (tab) => tab.index), [expected, expected + 1]);
    assert.equal(f.mutations.filter(([name]) => name === "groups.move").length, 1);
  }
});

test("native boundary adjustments can succeed without requiring the requested numeric index", async () => {
  for (const [constraint, adjusted] of [["pinned", 1], ["group", 0], ["group", 2]]) {
    const { f, id } = await movableGroup();
    // Put our group at the end, behind two tabs forming a constrained region.
    f.tabs.find((tab) => tab.id === 10).index = 0;
    f.tabs.find((tab) => tab.id === 11).index = 1;
    f.tabs.find((tab) => tab.id === 12).index = 2;
    f.tabs[0].index = 3; f.tabs[1].index = 4;
    if (constraint === "pinned") f.tabs.find((tab) => tab.id === 10).pinned = true;
    else for (const tabId of [10, 11]) f.tabs.find((tab) => tab.id === tabId).groupId = 200;
    const nativeMove = f.chrome.tabGroups.move;
    // Chrome variants may adjust or reject constrained indices. This case
    // models an adjusting API; the normal mock rejects group/pinned interiors.
    f.chrome.tabGroups.move = (groupId, args) => nativeMove(groupId, { ...args, index: adjusted });
    const result = await f.manage({ action: "move", group_id: id, index: constraint === "pinned" ? 0 : 1 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.result.actual_index, adjusted);
    assert.equal(f.mutations.filter(([name]) => name === "groups.move").length, 1);
  }
  const { f, id } = await movableGroup();
  f.tabs.find((tab) => tab.id === 10).pinned = true;
  f.chrome.tabGroups.move = async (groupId) => { f.mutations.push(["groups.move"]); return f.chrome.tabGroups.get(groupId); };
  const already = await f.manage({ action: "move", group_id: id, index: 0 });
  assert.equal(already.ok, true); assert.equal(already.result.actual_index, 1);
});

test("successful native no-op or wrong movement fails position verification without retries", async () => {
  for (const wrongIndex of [null, 3]) {
    const { f, id } = await movableGroup(), nativeMove = f.chrome.tabGroups.move;
    let attempts = 0;
    f.chrome.tabGroups.move = async (groupId, args) => {
      attempts++;
      return wrongIndex === null ? f.chrome.tabGroups.get(groupId) : nativeMove(groupId, { ...args, index: wrongIndex });
    };
    const result = await f.manage({ action: "move", group_id: id, index: 0 });
    assert.equal(result.error.code, "state_not_reached");
    assert.deepEqual(Array.from(result.error.details.progress.completed_steps), ["moved"]);
    assert.equal(attempts, 1);
  }
});

test("post-move identity/window/member/order/continuity changes reject success and never replay", async () => {
  for (const change of ["identity", "window", "member", "order", "gap", "strip", "unreadable"]) {
    const { f, id } = await movableGroup(), nativeMove = f.chrome.tabGroups.move;
    let attempts = 0;
    f.chrome.tabGroups.move = async (groupId, args) => {
      attempts++;
      const result = await nativeMove(groupId, args);
      if (change === "identity") result.id++;
      if (change === "window") f.groups[0].windowId = 7;
      if (change === "member") f.tabs[0].groupId = -1;
      if (change === "order") [f.tabs[0].index, f.tabs[1].index] = [f.tabs[1].index, f.tabs[0].index];
      if (change === "gap") f.tabs[1].index += 5;
      if (change === "strip") f.tabs.find((tab) => tab.id === 3).groupId = 200;
      if (change === "unreadable") f.chrome.tabGroups.get = async () => { throw Error("PRIVATE-FAILURE"); };
      return result;
    };
    const result = await f.manage({ action: "move", group_id: id, window_id: 8, index: -1 });
    assert.equal(result.error.code, change === "unreadable" ? "browser_operation_failed" : "state_not_reached", change);
    assert.equal(attempts, 1);
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
  }
});

test("mock refuses pinned/group interiors and expiry after move never retries", async () => {
  for (const constraint of ["pinned", "group"]) {
    const { f, id } = await movableGroup();
    if (constraint === "pinned") f.tabs.find((tab) => tab.id === 10).pinned = true;
    else for (const tabId of [11, 12]) f.tabs.find((tab) => tab.id === tabId).groupId = 200;
    const result = await f.manage({ action: "move", group_id: id, index: constraint === "pinned" ? 0 : 2 });
    assert.equal(result.error.code, "browser_operation_failed");
    assert.equal(f.mutations.filter(([name]) => name === "groups.move").length, 1);
  }
  let now = 0;
  const { f, id } = await movableGroup({ dateApi: { now: () => now } }), nativeMove = f.chrome.tabGroups.move;
  f.chrome.tabGroups.move = async (...args) => { const result = await nativeMove(...args); now = 1000; return result; };
  assert.equal((await f.call("browser.groups.manage", { action: "move", group_id: id, index: 0 }, 1000)).error.code, "request_expired");
  assert.equal(f.mutations.filter(([name]) => name === "groups.move").length, 1);
});

test("already at the clamped end succeeds and every member beyond summary pagination is checked", async () => {
  for (const index of [-1, 1000]) {
    const { f, id } = await movableGroup();
    await f.chrome.tabGroups.move(id, { windowId: 7, index: -1 });
    const result = await f.manage({ action: "move", group_id: id, index });
    assert.equal(result.ok, true); assert.equal(result.result.actual_index, 3);
    assert.equal(f.mutations.filter(([name]) => name === "groups.move").length, 2);
  }
  const f = browser(), id = await create(f);
  for (let index = 2; index < 70; index++) f.tabs.push({ ...f.tabs[0], id: 100 + index, index });
  const nativeMove = f.chrome.tabGroups.move;
  let attempts = 0;
  f.chrome.tabGroups.move = async (...args) => {
    attempts++; const result = await nativeMove(...args);
    f.tabs.find((tab) => tab.id === 169).index++;
    return result;
  };
  const result = await f.manage({ action: "move", group_id: id, window_id: 8, index: -1 });
  assert.equal(result.error.code, "state_not_reached"); assert.equal(attempts, 1);
});
