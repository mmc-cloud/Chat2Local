import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { bookmarks } from "./bookmarks-helper.mjs";

const ok = (response) => { assert.equal(response.ok, true, JSON.stringify(response)); return response.result; };
const error = (response, code) => { assert.equal(response.error?.code, code, JSON.stringify(response)); assert.equal(JSON.stringify(response).includes("PRIVATE-"), false); };
const deletions = (f) => f.mutations.filter(([name]) => name === "removeTree");
function bulk(f, parentId, count, extra = {}) {
  const parent = f.nodes.get(parentId);
  for (let n = 0; n < count; n++) {
    const id = `bulk${n}`;
    f.nodes.set(id, { id, title: "Same", parentId, index: parent.children.length, dateAdded: 10000 + n,
      url: "https://example.com/?token=PRIVATE-URL", ...extra }); parent.children.push(id);
  }
  parent.dateGroupModified++;
}

test("roots/get/children/search/recent are read-only, typed, ID-specific and sanitized", async () => {
  const f = bookmarks();
  const roots = ok(await f.query());
  assert.deepEqual(Array.from(roots.nodes, (n) => n.bookmark_id), ["root", "bar", "other", "managed"]);
  assert.equal(roots.nodes[0].parent_id, null);
  const leaf = ok(await f.query({ action: "get", bookmark_id: "a" })).nodes[0];
  assert.equal(leaf.type, "bookmark"); assert.equal(leaf.url, "https://example.com/a");
  assert.equal(leaf.parent_id, "folder"); assert.equal(leaf.index, 0); assert.equal(typeof leaf.date_added, "number");
  assert.equal(ok(await f.query({ action: "get", bookmark_id: "folder" })).nodes[0].type, "folder");
  assert.equal(ok(await f.query({ action: "get", bookmark_id: "specialurl" })).nodes[0].url_omitted, true);
  const same = ok(await f.query({ action: "search", title: "Same" }));
  assert.ok(same.nodes.some((n) => n.type === "folder")); assert.ok(same.nodes.some((n) => n.type === "bookmark"));
  assert.equal(new Set(same.nodes.map((n) => n.bookmark_id)).size, same.total);
  assert.equal(ok(await f.query({ action: "search", query: "example.com/a" })).nodes[0].bookmark_id, "a");
  assert.equal(ok(await f.query({ action: "search", url: f.nodes.get("a").url })).nodes[0].bookmark_id, "a");
  assert.equal(ok(await f.query({ action: "search", title: "Same", url: f.nodes.get("a").url })).total, 0);
  const recent = ok(await f.query({ action: "recent" }));
  assert.ok(recent.nodes.every((n) => n.type === "bookmark"));
  assert.equal(recent.nodes[0].bookmark_id, "b");
  error(await f.query({ action: "get", bookmark_id: "missing" }), "bookmark_not_found");
  error(await f.query({ action: "children", folder_id: "a" }), "bookmark_not_folder");
  assert.equal(JSON.stringify([leaf, roots, same, recent]).includes("PRIVATE-"), false);
  assert.equal(f.mutations.length, 0); assert.equal(f.calls.injected.length, 0);
});

test("modern roots reads only the advertised root and its immediate children", async () => {
  const f = bookmarks(), calls = [], nativeGet = f.chrome.bookmarks.get, nativeChildren = f.chrome.bookmarks.getChildren;
  f.chrome.bookmarks.ROOT_NODE_ID = "root";
  f.chrome.bookmarks.get = async (id) => { calls.push(["get", id]); return nativeGet(id); };
  f.chrome.bookmarks.getChildren = async (id) => { calls.push(["getChildren", id]); return nativeChildren(id); };
  f.chrome.bookmarks.getTree = async () => { assert.fail("Modern roots must not read the full tree"); };
  f.chrome.bookmarks.getSubTree = async () => { assert.fail("Modern roots must not read subtrees"); };
  const result = ok(await f.query());
  assert.deepEqual(calls, [["get", "root"], ["getChildren", "root"]]);
  assert.deepEqual(Array.from(result.nodes, (node) => [node.bookmark_id, node.depth, node.parent_id]),
    [["root", 0, null], ["bar", 1, "root"], ["other", 1, "root"], ["managed", 1, "root"]]);
  assert.equal(result.nodes[0].children_omitted, false);
  assert.equal(result.nodes[1].children_omitted, true);
  assert.equal(f.mutations.length, 0);
});

test("modern and legacy roots preserve dual stores, fields, order, pages and snapshots", async () => {
  const f = bookmarks(), calls = { get: 0, children: 0, tree: 0 };
  f.nodes.get("bar").syncing = false; f.nodes.get("other").syncing = false;
  f.add("account-bar", "root", { folderType: "bookmarks-bar", syncing: true });
  f.add("account-other", "root", { folderType: "other", syncing: true, title: "token=PRIVATE-ACCOUNT" });
  for (const [method, key] of [["get", "get"], ["getChildren", "children"], ["getTree", "tree"]]) {
    const native = f.chrome.bookmarks[method];
    f.chrome.bookmarks[method] = async (...args) => { calls[key]++; return native(...args); };
  }
  let args = { action: "roots", limit: 2 };
  const nodes = [];
  for (;;) {
    f.chrome.bookmarks.ROOT_NODE_ID = "root";
    const modern = ok(await f.query(args));
    delete f.chrome.bookmarks.ROOT_NODE_ID;
    const legacy = ok(await f.query(args));
    assert.deepEqual(modern, legacy); // Includes the same worker-keyed snapshot.
    assert.equal(modern.total, 6); assert.equal(modern.depth_limited, false);
    assert.equal(JSON.stringify(modern).includes("PRIVATE-"), false);
    nodes.push(...modern.nodes);
    if (!modern.has_more) { assert.equal(modern.next_offset, null); break; }
    args = { ...args, offset: modern.next_offset, snapshot: modern.snapshot };
  }
  assert.deepEqual(nodes.map((node) => node.bookmark_id), ["root", "bar", "other", "managed", "account-bar", "account-other"]);
  for (const folderType of ["bookmarks-bar", "other"]) {
    assert.deepEqual(nodes.filter((node) => node.folder_type === folderType).map((node) => node.syncing), [false, true]);
  }
  assert.deepEqual(calls, { get: 3, children: 3, tree: 3 });
  assert.equal(f.mutations.length, 0);
});

test("modern roots detects changes across pages without falling back to getTree", async () => {
  const f = bookmarks(); f.chrome.bookmarks.ROOT_NODE_ID = "root";
  f.chrome.bookmarks.getTree = async () => { assert.fail("Modern roots must not fall back to full-tree reads"); };
  const first = ok(await f.query({ limit: 2 }));
  f.add("account-bar", "root", { folderType: "bookmarks-bar", syncing: true });
  error(await f.query({ limit: 2, offset: first.next_offset, snapshot: first.snapshot }), "bookmark_changed");
});

test("modern roots does not visit large or deeply nested bookmark descendants", async () => {
  const f = bookmarks();
  let parent = "empty";
  for (let i = 0; i < 500; i++) { f.add(`deep${i}`, parent); parent = `deep${i}`; }
  bulk(f, parent, 20000);
  Object.defineProperty(f.nodes.get("bulk0"), "title", { get() { assert.fail("Descendants must not be traversed"); } });
  f.chrome.bookmarks.ROOT_NODE_ID = "root";
  const nativeGet = f.chrome.bookmarks.get, nativeChildren = f.chrome.bookmarks.getChildren;
  let reads = 0;
  f.chrome.bookmarks.get = async (id) => { assert.equal(id, "root"); reads++; return nativeGet(id); };
  f.chrome.bookmarks.getChildren = async (id) => { assert.equal(id, "root"); reads++; return nativeChildren(id); };
  f.chrome.bookmarks.getTree = async () => { assert.fail("Modern roots must not clone a large tree"); };
  f.chrome.bookmarks.getSubTree = async () => { assert.fail("Modern roots must not traverse a large subtree"); };
  assert.equal(ok(await f.query()).total, 4);
  assert.equal(reads, 2); assert.equal(f.mutations.length, 0);
});

test("large result pages continue beyond 50 and declare empty/last/out-of-range pages", async () => {
  const f = bookmarks(); bulk(f, "empty", 127);
  let args = { action: "children", folder_id: "empty" }, ids = [];
  for (const count of [50, 50, 27]) {
    const page = ok(await f.query(args));
    assert.equal(page.total, 127); assert.equal(page.nodes.length, count); ids.push(...page.nodes.map((n) => n.bookmark_id));
    assert.equal(page.has_more, ids.length < 127);
    args = { ...args, offset: page.next_offset ?? 127, snapshot: page.snapshot };
  }
  assert.equal(new Set(ids).size, 127);
  assert.equal(ok(await f.query(args)).nodes.length, 0);
  error(await f.query({ ...args, offset: 128 }), "invalid_arguments");
  error(await f.query({ ...args, snapshot: undefined, offset: 50 }), "invalid_arguments");
  for (const action of ["search", "recent", "subtree"]) {
    const page = ok(await f.query({ action, ...(action === "search" ? { title: "Same" } : action === "subtree" ? { folder_id: "empty" } : {}) }));
    assert.ok(page.total > 50); assert.equal(page.has_more, true);
  }
});

for (const change of ["add", "remove", "title", "url", "order", "scope", "restart"]) test(`pagination rejects changed ${change}`, async () => {
  const f = bookmarks(); bulk(f, "empty", 70);
  const page = ok(await f.query({ action: "children", folder_id: "empty" }));
  const args = { action: "children", folder_id: "empty", offset: 50, snapshot: page.snapshot };
  if (change === "add") f.add("new", "empty", { url: "https://new.example/" });
  if (change === "remove") f.erase(f.nodes.get("bulk0"));
  if (change === "title") f.nodes.get("bulk0").title = "Changed";
  if (change === "url") f.nodes.get("bulk0").url += "#changed";
  if (change === "order") {
    f.nodes.get("empty").children.reverse();
    f.nodes.get("empty").children.forEach((id, index) => { f.nodes.get(id).index = index; });
  }
  if (change === "scope") args.folder_id = "folder";
  error(await (change === "restart" ? bookmarks() : f).query(args), "bookmark_changed");
  assert.equal(f.mutations.length, 0);
});

test("subtree gives depth-limited pre-order rows with explicit continuation folders", async () => {
  const f = bookmarks();
  const limited = ok(await f.query({ action: "subtree", folder_id: "folder", depth: 1 }));
  assert.equal(limited.total, 3); assert.equal(limited.depth_limited, true);
  assert.deepEqual(Array.from(limited.nodes, (n) => [n.bookmark_id, n.depth]), [["folder", 0], ["a", 1], ["nested", 1]]);
  assert.equal(limited.nodes[2].children_omitted, true);
  assert.equal(ok(await f.query({ action: "children", folder_id: "nested" })).nodes[0].bookmark_id, "b");
  const full = ok(await f.query({ action: "subtree", folder_id: "folder", depth: 2 }));
  assert.equal(full.total, 4); assert.equal(full.depth_limited, false);
  assert.equal(ok(await f.query({ action: "subtree", folder_id: "folder", depth: 0 })).total, 1);
});

test("query and preview honor response byte budgets and allow smaller pages/examples", async () => {
  const f = bookmarks(); bulk(f, "empty", 50, { title: "长".repeat(512), url: "https://example.com/" + "a/".repeat(900) });
  error(await f.query({ action: "children", folder_id: "empty" }), "result_too_large");
  assert.equal(ok(await f.query({ action: "children", folder_id: "empty", limit: 1 })).total, 50);
  error(await f.preview({ folder_id: "empty", examples_limit: 20 }), "result_too_large");
  assert.equal(ok(await f.preview({ folder_id: "empty", examples_limit: 1 })).total_nodes, 51);
  assert.equal(f.mutations.length, 0);
});

test("create/update/rename use actual native results and preserve legacy URL organization", async () => {
  const f = bookmarks();
  const created = ok(await f.manage({ action: "create", kind: "bookmark", parent_id: "bar", title: "token=PRIVATE-TITLE", url: "https://example.com/?token=PRIVATE-URL", index: 0 }));
  assert.equal(created.node.parent_id, "bar"); assert.equal(created.node.index, 0);
  assert.equal(created.node.url, "https://example.com/"); assert.equal(created.removed, false);
  const createdFolder = ok(await f.manage({ action: "create", kind: "folder", parent_id: "other", title: "Folder" }));
  assert.equal(createdFolder.node.type, "folder"); assert.equal(createdFolder.node.url, null);
  assert.equal(ok(await f.manage({ action: "update", bookmark_id: createdFolder.node.bookmark_id, title: "" })).node.title, "");
  const updated = ok(await f.manage({ action: "update", bookmark_id: created.node.bookmark_id, title: "Changed", url: "https://next.example/path" }));
  assert.equal(updated.node.url, "https://next.example/path");
  assert.equal(ok(await f.manage({ action: "update", bookmark_id: "specialurl", title: "Legacy" })).node.title, "Legacy");
  error(await f.manage({ action: "update", bookmark_id: "folder", url: "https://example.com/" }), "bookmark_not_bookmark");
  assert.equal(JSON.stringify(created).includes("PRIVATE-"), false);
  for (const url of ["chrome://bookmarks/", "chrome://BOOKMARKS", "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef",
    "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef/options.html", "file:///C:/temp/a.html", "ftp://example.com/file", "about:blank"]) {
    assert.equal(ok(await f.manage({ action: "create", kind: "bookmark", parent_id: "other", title: "Special", url })).node.type, "bookmark");
  }
});

test("move verifies parent, same-parent insertion adjustment, append and native clamp", async () => {
  const f = bookmarks();
  const forward = ok(await f.manage({ action: "move", bookmark_id: "bookmark", parent_id: "bar", index: 4 }));
  assert.equal(forward.node.index, 3); // Pre-removal insertion index shifts left.
  const moved = ok(await f.manage({ action: "move", bookmark_id: "folder", parent_id: "other" }));
  assert.equal(moved.node.parent_id, "other"); assert.equal(moved.node.index, 0);
  assert.equal(ok(await f.manage({ action: "move", bookmark_id: "specialurl", parent_id: "other", index: 0 })).node.index, 0);
  const native = f.chrome.bookmarks.move;
  f.chrome.bookmarks.move = (id, args) => native(id, { ...args, index: f.nodes.get(args.parentId).children.length });
  assert.equal(ok(await f.manage({ action: "move", bookmark_id: "bookmark", parent_id: "other", index: 100 })).node.index, 2);
  assert.equal(f.mutations.filter(([name]) => name === "move").length, 4);
});

test("cycle/non-folder/invalid native position and protected nodes fail without fallback", async () => {
  const f = bookmarks();
  for (const parent_id of ["folder", "nested"]) error(await f.manage({ action: "move", bookmark_id: "folder", parent_id }), "bookmark_invalid_move");
  error(await f.manage({ action: "move", bookmark_id: "a", parent_id: "bookmark" }), "bookmark_not_folder");
  for (const bookmark_id of ["root", "bar", "other", "managed", "policy"]) {
    for (const args of [{ action: "update", bookmark_id, title: "No" }, { action: "move", bookmark_id, parent_id: "empty" }, { action: "remove", bookmark_id }]) {
      error(await f.manage(args), "bookmark_protected");
    }
  }
  // Legacy permanent folders are recognized structurally, with non-numeric IDs.
  delete f.nodes.get("bar").folderType;
  error(await f.manage({ action: "update", bookmark_id: "bar", title: "No" }), "bookmark_protected");
  for (const parent_id of ["root", "managed"]) error(await f.manage({ action: "create", kind: "folder", parent_id, title: "No" }), "bookmark_protected");
  assert.equal(f.mutations.length, 0);
  error(await f.manage({ action: "move", bookmark_id: "a", parent_id: "empty", index: 999 }), "bookmark_outcome_unknown");
  assert.equal(f.mutations.length, 1);
});

test("ordinary remove allows leaves and empty folders, refuses nonempty and verifies absence", async () => {
  const f = bookmarks();
  error(await f.manage({ action: "remove", bookmark_id: "folder" }), "bookmark_not_empty");
  assert.equal(f.mutations.length, 0);
  for (const bookmark_id of ["a", "empty"]) {
    assert.equal(ok(await f.manage({ action: "remove", bookmark_id })).removed, true);
    assert.equal(f.nodes.has(bookmark_id), false);
  }
  error(await f.manage({ action: "remove", bookmark_id: "a" }), "bookmark_not_found");
  assert.equal(f.mutations.length, 2);
});

test("full preview counts all nodes while showing bounded examples and performs no write", async () => {
  const f = bookmarks(); bulk(f, "nested", 123);
  const result = ok(await f.preview({ examples_limit: 2 }));
  assert.equal(result.bookmark_count, 125); assert.equal(result.subfolder_count, 1); assert.equal(result.total_nodes, 127);
  assert.equal(result.examples.length, 2); assert.equal(result.has_more, true);
  assert.equal(result.folder.parent_id, "bar"); assert.match(result.preview_id, /^bmk_[0-9a-f-]{36}$/);
  assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
  assert.equal(f.mutations.length, 0);
  const removed = ok(await f.removeTree(result.preview_id));
  assert.equal(removed.total_nodes, 127); assert.equal(removed.removed, true); assert.equal(f.nodes.has("folder"), false);
  assert.equal(deletions(f).length, 1);
  error(await f.removeTree(result.preview_id), "bookmark_preview_unavailable");
  assert.equal(deletions(f).length, 1);
});

for (const change of ["add", "delete", "title", "url", "move", "folder-title", "folder-move", "order", "identity", "protection"]) test(`recursive delete refuses preview after ${change}`, async () => {
  const f = bookmarks(), preview = ok(await f.preview());
  if (change === "add") f.add("extra", "nested", { url: "https://new.example/" });
  if (change === "delete") f.erase(f.nodes.get("a"));
  if (change === "title") f.nodes.get("a").title = "Changed";
  if (change === "url") f.nodes.get("b").url += "?changed";
  if (change === "move") await f.chrome.bookmarks.move("b", { parentId: "other" });
  if (change === "folder-title") f.nodes.get("folder").title = "Changed";
  if (change === "folder-move") await f.chrome.bookmarks.move("folder", { parentId: "other" });
  if (change === "order") {
    f.nodes.get("folder").children.reverse();
    f.nodes.get("folder").children.forEach((id, index) => { f.nodes.get(id).index = index; });
  }
  if (change === "identity") f.nodes.get("a").dateAdded++;
  if (change === "protection") f.nodes.get("nested").unmodifiable = "managed";
  error(await f.removeTree(preview.preview_id), change === "protection" ? "bookmark_protected" : "bookmark_changed");
  assert.equal(deletions(f).length, 0);
});

test("wrong/expired/evicted/restarted preview credentials never dispatch removeTree", async () => {
  let now = 0;
  const f = bookmarks({ dateApi: { now: () => now } }), preview = ok(await f.preview());
  error(await f.removeTree(preview.preview_id, "empty"), "bookmark_preview_unavailable");
  error(await f.removeTree("bmk_00000000-0000-0000-0000-000000000000"), "bookmark_preview_unavailable");
  error(await bookmarks().removeTree(preview.preview_id), "bookmark_preview_unavailable");
  now = 120000;
  error(await f.removeTree(preview.preview_id), "bookmark_preview_unavailable");
  const oldest = ok(await f.preview()).preview_id;
  for (let i = 0; i < 32; i++) ok(await f.preview());
  error(await f.removeTree(oldest), "bookmark_preview_unavailable");
  assert.equal(deletions(f).length, 0);
});

test("protected roots/special/managed trees and protected descendants cannot be previewed", async () => {
  const f = bookmarks();
  for (const folder_id of ["root", "bar", "other", "managed"]) error(await f.preview({ folder_id }), "bookmark_protected");
  error(await f.preview({ folder_id: "a" }), "bookmark_not_folder");
  f.nodes.get("nested").folderType = "other";
  error(await f.preview(), "bookmark_protected");
  error(await f.manage({ action: "move", bookmark_id: "folder", parent_id: "other" }), "bookmark_protected");
  delete f.nodes.get("nested").folderType; f.nodes.get("bar").unmodifiable = "managed";
  error(await f.preview(), "bookmark_protected");
  assert.equal(f.mutations.length, 0);
});

test("complete traversal bounds issue no credentials for partial node/depth/byte scopes", async () => {
  for (const limit of ["nodes", "depth", "bytes", "incomplete"]) {
    const f = bookmarks();
    if (limit === "nodes") bulk(f, "empty", 10000);
    if (limit === "depth") { let parent = "empty"; for (let i = 0; i < 65; i++) { f.add(`deep${i}`, parent); parent = `deep${i}`; } }
    if (limit === "bytes") f.add("huge", "empty", { url: "https://example.com/" + "a".repeat(2 * 1024 * 1024) });
    if (limit === "incomplete") {
      const native = f.chrome.bookmarks.getSubTree;
      f.chrome.bookmarks.getSubTree = async (id) => { const result = await native(id); delete result[0].children; return result; };
    }
    const result = await f.preview({ folder_id: "empty" });
    error(result, limit === "incomplete" ? "bookmark_changed" : "bookmark_traversal_limit");
    assert.equal(result.result?.preview_id, undefined); assert.equal(f.mutations.length, 0);
  }
  const f = bookmarks(); bulk(f, "empty", 9999);
  assert.equal(ok(await f.preview({ folder_id: "empty", examples_limit: 1 })).total_nodes, 10000);
});

test("all bookmark capabilities require BOTH independent permission gates", async () => {
  for (const [granted, consent] of [[false, true], [true, false], [false, false]]) {
    const f = bookmarks({ granted, consent }); f.settings.groupAccess = true; f.settings.allWebsites = true;
    for (const response of [await f.query(), await f.manage({ action: "remove", bookmark_id: "a" }), await f.preview(),
      await f.removeTree("bmk_00000000-0000-0000-0000-000000000000")]) error(response, "bookmarks_permission_denied");
    assert.equal(f.mutations.length, 0);
    assert.equal((await f.call("browser.tabs.list", {})).ok, true);
  }
});

test("permission revocation and deadlines during final tree recheck block deletion", async () => {
  for (const cause of ["consent", "permission", "deadline"]) {
    let now = 0;
    const f = bookmarks({ dateApi: { now: () => now } }), preview = ok(await f.preview()), native = f.chrome.bookmarks.getSubTree;
    f.chrome.bookmarks.getSubTree = async (id) => {
      const result = await native(id);
      if (cause === "consent") f.settings.bookmarkAccess = false;
      if (cause === "permission") f.permission.granted = false;
      if (cause === "deadline") now = 1000;
      return result;
    };
    const result = await f.call("browser.bookmarks.remove_tree", { folder_id: "folder", preview_id: preview.preview_id }, 1000);
    error(result, cause === "deadline" ? "request_expired" : "bookmarks_permission_denied");
    assert.equal(deletions(f).length, 0);
  }
});

test("recursive delete rechecks protected ancestry after snapshot comparison", async () => {
  const f = bookmarks(), preview = ok(await f.preview()), native = f.chrome.bookmarks.get;
  let reads = 0;
  f.chrome.bookmarks.get = async (id) => {
    if (id === "folder" && ++reads === 2) f.nodes.get("bar").unmodifiable = "managed";
    return native(id);
  };
  error(await f.removeTree(preview.preview_id), "bookmark_protected");
  assert.equal(deletions(f).length, 0);
});

test("deletion uncertainty consumes credential and never repeats even after native partial success", async () => {
  for (const failure of ["native", "post-read", "no-effect", "expiry"]) {
    let now = 0;
    const f = bookmarks({ dateApi: { now: () => now } }), preview = ok(await f.preview()), native = f.chrome.bookmarks.removeTree;
    let attempts = 0;
    f.chrome.bookmarks.removeTree = async (id) => {
      attempts++;
      if (failure !== "no-effect") await native(id);
      if (failure === "native") throw Error("https://example.com/?token=PRIVATE-ERROR");
      if (failure === "post-read") f.chrome.bookmarks.get = async () => { throw Error("PRIVATE-ERROR"); };
      if (failure === "expiry") now = 1000;
    };
    const result = await f.call("browser.bookmarks.remove_tree", { folder_id: "folder", preview_id: preview.preview_id }, 1000);
    error(result, failure === "no-effect" ? "state_not_reached" : failure === "expiry" ? "request_expired" : "bookmark_outcome_unknown");
    error(await f.removeTree(preview.preview_id), "bookmark_preview_unavailable");
    assert.equal(attempts, 1);
  }
});

test("concurrent recursive deletes with one credential dispatch at most one native delete", async () => {
  const f = bookmarks(), preview = ok(await f.preview());
  const results = await Promise.all([f.removeTree(preview.preview_id), f.removeTree(preview.preview_id)]);
  assert.equal(results.filter((r) => r.ok).length, 1);
  error(results.find((r) => !r.ok), "bookmark_preview_unavailable");
  assert.equal(deletions(f).length, 1);
});

test("an attempt also invalidates other previews for that folder", async () => {
  const f = bookmarks(), first = ok(await f.preview()), second = ok(await f.preview());
  f.chrome.bookmarks.removeTree = async (id) => { f.mutations.push(["removeTree", id]); throw Error("PRIVATE-ERROR"); };
  error(await f.removeTree(first.preview_id), "bookmark_outcome_unknown");
  error(await f.removeTree(second.preview_id), "bookmark_preview_unavailable");
  assert.equal(deletions(f).length, 1);
});

test("ordinary writes reject changed targets and uncertain postconditions without retries", async () => {
  for (const action of ["update", "move", "remove", "create"]) {
    const f = bookmarks(), native = f.chrome.bookmarks[action];
    let attempts = 0;
    f.chrome.bookmarks[action] = async (...args) => { attempts++; await native(...args); throw Error("PRIVATE-ERROR"); };
    const args = action === "create" ? { action, kind: "folder", title: "New", parent_id: "bar" }
      : action === "update" ? { action, bookmark_id: "a", title: "New" } : action === "move" ? { action, bookmark_id: "a", parent_id: "empty" } : { action, bookmark_id: "a" };
    error(await f.manage(args), "bookmark_outcome_unknown"); assert.equal(attempts, 1);
  }
  const f = bookmarks(), native = f.chrome.bookmarks.get;
  let reads = 0;
  f.chrome.bookmarks.get = async (id) => {
    if (id === "a" && ++reads === 2) f.nodes.get(id).title = "Changed";
    return native(id);
  };
  error(await f.manage({ action: "remove", bookmark_id: "a" }), "bookmark_changed");
  assert.equal(f.mutations.length, 0);
});

test("successful no-op APIs cannot claim unachieved update/move/remove results", async () => {
  for (const action of ["update", "move", "remove"]) {
    const f = bookmarks(); let attempts = 0;
    f.chrome.bookmarks[action] = async (id) => { attempts++; return (await f.chrome.bookmarks.get(id))[0]; };
    const args = action === "update" ? { action, bookmark_id: "a", title: "New" }
      : action === "move" ? { action, bookmark_id: "a", parent_id: "folder", index: 2 } : { action, bookmark_id: "a" };
    error(await f.manage(args), "state_not_reached"); assert.equal(attempts, 1);
  }
});

test("special display URLs retain safe locations without credentials, query, fragment or secret paths", async () => {
  const f = bookmarks();
  for (const url of ["ftp://user:PRIVATE-CREDENTIAL@example.com/public?token=PRIVATE-QUERY#PRIVATE-FRAGMENT",
    "chrome://settings/public?token=PRIVATE-QUERY#PRIVATE-FRAGMENT", "file:///C:/token/PRIVATE-PATH",
    "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef/options.html?token=PRIVATE-QUERY"]) {
    f.nodes.get("specialurl").url = url;
    const result = ok(await f.query({ action: "get", bookmark_id: "specialurl" }));
    assert.notEqual(result.nodes[0].url, null);
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
    assert.ok(result.nodes[0].url.length <= 2048);
  }
});

test("post-write unreadable or changed nodes are explicitly uncertain and never repeated", async () => {
  const f = bookmarks(), native = f.chrome.bookmarks.update;
  let attempts = 0;
  f.chrome.bookmarks.update = async (...args) => {
    attempts++; const result = await native(...args);
    f.chrome.bookmarks.get = async () => [{ ...result, id: "different" }];
    return result;
  };
  error(await f.manage({ action: "update", bookmark_id: "a", title: "Changed" }), "bookmark_outcome_unknown");
  assert.equal(attempts, 1);
});

test("type changes and disappearance during remove preparation dispatch no native delete", async () => {
  for (const change of ["type", "missing"]) {
    const f = bookmarks(), native = f.chrome.bookmarks.get;
    let reads = 0;
    f.chrome.bookmarks.get = async (id) => {
      if (id === "a" && ++reads === 2) {
        if (change === "type") { delete f.nodes.get(id).url; f.nodes.get(id).children = []; }
        else f.erase(f.nodes.get(id));
      }
      return native(id);
    };
    error(await f.manage({ action: "remove", bookmark_id: "a" }), change === "type" ? "bookmark_changed" : "bookmark_not_found");
    assert.equal(f.mutations.length, 0);
  }
});

test("ordinary writes recheck permission immediately before dispatch and queries sanitize native failures", async () => {
  for (const args of [{ action: "create", kind: "folder", parent_id: "bar", title: "New" },
    { action: "update", bookmark_id: "a", title: "New" }, { action: "move", bookmark_id: "a", parent_id: "empty" },
    { action: "remove", bookmark_id: "a" }]) {
    const f = bookmarks(); let checks = 0;
    f.chrome.permissions.contains = async () => ++checks === 1;
    error(await f.manage(args), "bookmarks_permission_denied");
    assert.equal(f.mutations.length, 0);
  }
  const f = bookmarks();
  f.chrome.bookmarks.search = async () => { throw Error("PRIVATE-TITLE https://example.com/?token=PRIVATE-URL"); };
  error(await f.query({ action: "search", query: "Example" }), "browser_operation_failed");
  assert.equal(f.mutations.length, 0);
});

test("bookmark success keys match published result contracts", async () => {
  const f = bookmarks(), schema = JSON.parse(readFileSync(new URL("../capability-schemas.json", import.meta.url), "utf8"));
  const preview = await f.preview();
  const results = [["query", await f.query()], ["manage", await f.manage({ action: "update", bookmark_id: "empty", title: "" })],
    ["preview_remove_tree", preview], ["remove_tree", await f.removeTree(ok(preview).preview_id)]];
  for (const [name, response] of results) {
    const contract = schema.capabilities.find((item) => item.name === `browser.bookmarks.${name}`).result_contract;
    assert.deepEqual(Object.keys(ok(response)).sort(), Object.keys(contract).sort());
    assert.equal(JSON.stringify(response).includes("PRIVATE-"), false);
  }
});
