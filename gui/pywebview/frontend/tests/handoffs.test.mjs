import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import { JSDOM } from "jsdom";
import Markdown from "react-markdown";

const require = createRequire(import.meta.url);
const React = require("react");
const ts = require("typescript");
const { URL, setTimeout, clearTimeout } = globalThis;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const entry = (key, name, title = name, scope = "workspace") => ({
  id: `${key}:design`, project: { key, name, scope, path: scope === "personal" ? null : `/gone/${name}` },
  title, summary: `Summary ${title}`, workstream: "design", revision: 3, updated_at: "2026-10-25T01:00:00Z",
});

async function fixture(language = "en", web = false) {
  const dom = new JSDOM('<div id="root"></div>', { url: "https://gui.test/" });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.HTMLElement = dom.window.HTMLElement;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  dom.window.localStorage.setItem("chat2local.gui.language.cache", language);
  const { createRoot } = require("react-dom/client");
  const root = createRoot(globalThis.document.getElementById("root"));
  const calls = [];
  const copied = [], confirmations = [];
  let confirmResult = false, leaveGuard;
  dom.window.confirm = (message) => { confirmations.push(message); return confirmResult; };
  const navigator = { clipboard: { writeText: async (value) => copied.push(value) } };
  const api = {
    localHandoffs: async () => ({ handoffs: [], issues: [] }),
    localHandoff: async (id) => {
      calls.push(id);
      return { ...entries.find((e) => e.id === id), content: "# Body\n\n**Read only**\n\n- First\n- Second" };
    },
    saveLocalHandoff: async (id, title, summary, content, revision) => {
      calls.push(["save", id, title, summary, content, revision]);
      return { ...entries.find((e) => e.id === id), title, summary, content, revision: revision + 1, updated_at: "2026-10-25T03:00:00Z" };
    },
  };
  const entries = [entry("a", "Same project", "Alpha"), entry("b", "Same project", "Beta"),
    entry("p", "Personal", "Private note", "personal")];
  const native = (tag) => (props) => React.createElement(tag, props, props.children);
  const mocks = {
    "@/lib/bridge": { coreApi: api, isWebUi: web, desktopApi: {}, errorMessage: (error) => error.message },
    "@/components/ui/button": { Button: native("button") },
    "@/components/ui/input": { Input: native("input") },
    "lucide-react": { RefreshCw: () => null, ChevronRight: () => null },
    "react-markdown": { __esModule: true, default: Markdown },
    "@/components/ui/tooltip": { TooltipProvider: ({ children }) => children },
    "sonner": { Toaster: () => null },
    "@/lib/poll": { usePoll: () => ({ data: { lifecycle: "Stopped", core: null } }) },
    "@/pages/overview": { Overview: () => React.createElement("p", null, "Overview page") },
    "@/pages/devices": { Devices: () => null },
    "@/pages/settings": { Settings: () => React.createElement("p", null, "Settings page") },
    "@/pages/logs": { Logs: () => React.createElement("p", null, "Logs page") },
    "@/components/app-sidebar": { AppSidebar: ({ onNavigate }) => React.createElement("nav", null,
      ...["Overview", "Handoffs", "Settings", "Logs"].map((page) => React.createElement("button", { key: page, onClick: () => onNavigate(page) }, page))) },
  };
  mocks["@/lib/bridge"].useBridge = () => ({ status: "connected" });
  function load(name) {
    if (mocks[name]) return mocks[name];
    if (!name.startsWith("@/")) return require(name);
    const extension = name === "@/lib/locale" ? "ts" : "tsx";
    const source = readFileSync(new URL(`../src/${name.slice(2)}.${extension}`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    const module = { exports: {} };
    vm.runInNewContext(code, { require: load, module, exports: module.exports, document: dom.window.document,
      window: dom.window, navigator, localStorage: dom.window.localStorage, setTimeout, clearTimeout });
    return module.exports;
  }
  const { Handoffs } = load("@/pages/handoffs");
  const render = () => React.act(async () => root.render(React.createElement(Handoffs, { onLeaveGuard: (next) => { leaveGuard = next; } })));
  const renderApp = () => React.act(async () => root.render(React.createElement(load("@/app").App)));
  const click = async (button) => { assert.ok(button); await React.act(async () => button.click()); };
  const toggleGroup = async (index) => click(globalThis.document.querySelectorAll(".handoff-group-toggle")[index]);
  const search = async (value) => React.act(async () => {
    const input = globalThis.document.querySelector("input");
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  const close = async () => { await React.act(async () => root.unmount()); dom.window.close(); };
  const field = async (selector, value) => React.act(async () => {
    const input = globalThis.document.querySelector(selector);
    assert.ok(input);
    const prototype = input.tagName === "TEXTAREA" ? dom.window.HTMLTextAreaElement.prototype : dom.window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(input, value);
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  const button = (text) => [...globalThis.document.querySelectorAll("button")].find((node) => node.textContent === text);
  const beforeUnload = () => { const event = new dom.window.Event("beforeunload", { cancelable: true }); dom.window.dispatchEvent(event); return event.defaultPrevented; };
  const submit = () => React.act(async () => globalThis.document.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true })));
  return { api, entries, calls, render, renderApp, click, toggleGroup, search, close, field, button, beforeUnload, submit,
    copied, navigator, confirmations, confirm: (value) => { confirmResult = value; }, canLeave: () => leaveGuard?.() ?? true };
}

for (const web of [false, true]) test(`Handoffs group by identity and render selected metadata and Markdown (${web ? "HTTP" : "GUI"})`, async () => {
  const f = await fixture("en", web);
  try {
    f.api.localHandoffs = async () => ({ handoffs: f.entries, issues: [{ location: "broken.md", message: "invalid header" }] });
    await f.render();
    assert.equal(globalThis.document.querySelectorAll(".handoff-group").length, 3);
    assert.match(globalThis.document.querySelector(".handoff-group h2").textContent, /Personal/);
    assert.match(globalThis.document.querySelector("details").textContent, /broken.md.*invalid header/);
    assert.equal(globalThis.document.querySelectorAll(".handoff-item").length, 0);
    await f.toggleGroup(2);
    assert.equal(globalThis.document.querySelectorAll(".handoff-group-toggle")[2].getAttribute("aria-expanded"), "true");
    await f.click(globalThis.document.querySelector(".handoff-item"));
    assert.deepEqual(f.calls, ["b:design"]);
    assert.match(globalThis.document.querySelector(".handoff-metadata").textContent, /Same project.*design.*Summary Beta.*3.*2026-10-25/s);
    assert.equal(globalThis.document.querySelector(".handoff-markdown h1").textContent, "Body");
    assert.equal(globalThis.document.querySelector(".handoff-markdown strong").textContent, "Read only");
    assert.equal(globalThis.document.querySelectorAll(".handoff-markdown li").length, 2);
    for (const query of ["alpha", "summary beta", "same project", "PRIVATE"]) {
      await f.search(query);
      assert.equal(globalThis.document.querySelectorAll(".handoff-item").length, query === "same project" ? 2 : 1);
    }
    await f.search("same project");
    await f.toggleGroup(1);
    assert.equal(globalThis.document.querySelectorAll(".handoff-item").length, 1);
    await f.toggleGroup(1);
    assert.equal(globalThis.document.querySelectorAll(".handoff-item").length, 2);
    await f.search("not found");
    assert.match(globalThis.document.body.textContent, /No handoffs match/);
    await f.search("");
    f.api.localHandoffs = async () => ({ handoffs: [], issues: [] });
    await f.click([...globalThis.document.querySelectorAll("button")].find((button) => button.textContent === "Refresh"));
    assert.match(globalThis.document.body.textContent, /No local handoffs yet/);
    assert.equal(globalThis.document.querySelector(".handoff-detail h2"), null);
  } finally { await f.close(); }
});

test("Loading, list error, retry and Chinese empty state are visible", async () => {
  const f = await fixture("zh-CN");
  const pending = deferred();
  try {
    f.api.localHandoffs = () => pending.promise;
    await f.render();
    assert.match(globalThis.document.body.textContent, /正在加载交接状态列表/);
    assert.equal(globalThis.document.querySelector("button").disabled, true);
    await React.act(async () => pending.reject(new Error("Read failed")));
    assert.equal(globalThis.document.querySelector('[role="alert"]').textContent, "Read failed");
    f.api.localHandoffs = async () => ({ handoffs: [], issues: [] });
    await f.click(globalThis.document.querySelector("button"));
    assert.match(globalThis.document.body.textContent, /本机暂无交接状态/);
    assert.equal(globalThis.document.querySelector('[role="alert"]'), null);
  } finally { await f.close(); }
});

test("Late detail responses cannot replace current selection; deleted files show a retryable error", async () => {
  const f = await fixture();
  const pending = deferred();
  try {
    f.api.localHandoffs = async () => ({ handoffs: f.entries, issues: [] });
    f.api.localHandoff = (id) => id === "a:design" ? pending.promise : Promise.reject(new Error("handoff_not_found: design"));
    await f.render();
    await f.toggleGroup(1);
    await f.click(globalThis.document.querySelector(".handoff-item"));
    assert.match(globalThis.document.querySelector(".handoff-detail").textContent, /Loading handoff/);
    globalThis.document.querySelector(".handoff-detail").scrollTop = 120;
    await f.toggleGroup(2);
    await f.click(globalThis.document.querySelectorAll(".handoff-item")[1]);
    assert.equal(globalThis.document.querySelector(".handoff-detail").scrollTop, 0);
    assert.match(globalThis.document.querySelector('[role="alert"]').textContent, /moved or been deleted.*handoff_not_found/s);
    await React.act(async () => pending.resolve({ ...f.entries[0], content: "STALE CONTENT" }));
    assert.equal(globalThis.document.body.textContent.includes("STALE CONTENT"), false);
    f.api.localHandoff = async () => ({ ...f.entries[1], content: "Recovered" });
    await f.click([...globalThis.document.querySelectorAll("button")].find((button) => button.textContent === "Refresh"));
    assert.match(globalThis.document.querySelector(".handoff-markdown").textContent, /Recovered/);
  } finally { await f.close(); }
});

test("Markdown preview never creates HTML scripts, images or navigable external links", async () => {
  const f = await fixture();
  try {
    f.api.localHandoffs = async () => ({ handoffs: f.entries, issues: [] });
    f.api.localHandoff = async () => ({ ...f.entries[0], content:
      '<script>alert(1)</script>\n\n<iframe src="https://evil.test"></iframe>\n\n<img src=x onerror=alert(1)>\n\n![tracker](https://evil.test/x)\n\n[bad](javascript:alert%281%29) [local](file:///etc/passwd) [web](https://evil.test) [anchor](#body)' });
    await f.render();
    await f.toggleGroup(1);
    await f.click(globalThis.document.querySelector(".handoff-item"));
    const preview = globalThis.document.querySelector(".handoff-markdown");
    assert.equal(preview.querySelector("script, iframe, img, [onerror]"), null);
    assert.deepEqual([...preview.querySelectorAll("a")].map((a) => a.getAttribute("href")), ["#body"]);
  } finally { await f.close(); }
});

async function editFixture(web = false, language = "en", app = false) {
  const f = await fixture(language, web);
  f.api.localHandoffs = async () => ({ handoffs: f.entries, issues: [] });
  if (app) { await f.renderApp(); await f.click(f.button("Handoffs")); }
  else await f.render();
  await f.toggleGroup(1);
  await f.click(globalThis.document.querySelector(".handoff-item"));
  await f.click(f.button(language === "en" ? "Edit handoff" : "编辑交接状态"));
  return f;
}

for (const web of [false, true]) test(`Editing saves all three fields and updates the reader and list (${web ? "Web" : "GUI"})`, async () => {
  const f = await editFixture(web);
  try {
    assert.equal(f.beforeUnload(), false);
    await f.field("#handoff-title", "New title");
    await f.field("#handoff-summary", "New summary");
    await f.field("#handoff-content", "# New body\n\n正文");
    assert.equal(f.beforeUnload(), true);
    assert.match(globalThis.document.querySelector(".handoff-metadata").textContent, /design.*3/s);
    await f.click(f.button("Save changes"));
    assert.deepEqual(f.calls.at(-1), ["save", "a:design", "New title", "New summary", "# New body\n\n正文", 3]);
    assert.equal(globalThis.document.querySelector("form"), null);
    assert.match(globalThis.document.querySelector(".handoff-metadata").textContent, /New summary.*4.*2026-10-25T03/s);
    assert.equal(globalThis.document.querySelector(".handoff-item strong").textContent, "New title");
    assert.equal(globalThis.document.querySelector(".handoff-item span").textContent, "New summary");
    assert.equal(globalThis.document.querySelector(".handoff-markdown h1").textContent, "New body");
    assert.equal(f.beforeUnload(), false);
    assert.equal(f.canLeave(), true);
    assert.equal(f.confirmations.length, 0);
  } finally { await f.close(); }
});

for (const action of ["switch", "refresh", "cancel", "leave"]) test(`Unsaved changes require confirmation for ${action}`, async () => {
  const f = await editFixture();
  try {
    await f.field("#handoff-title", "Draft title");
    if (action === "switch") await f.toggleGroup(2);
    const perform = () => action === "switch" ? f.click(globalThis.document.querySelectorAll(".handoff-item")[1])
      : action === "refresh" ? f.click(f.button("Refresh")) : action === "cancel" ? f.click(f.button("Cancel editing")) : f.canLeave();
    assert.equal(f.beforeUnload(), true);
    await perform();
    assert.equal(globalThis.document.getElementById("handoff-title").value, "Draft title");
    assert.equal(f.confirmations.length, 1);
    f.confirm(true);
    const accepted = await perform();
    if (action === "leave") assert.equal(accepted, true);
    else assert.equal(globalThis.document.querySelector("form"), null);
    assert.equal(f.confirmations.length, 2);
  } finally { await f.close(); }
});

test("Opening an editor or reverting edits does not cause a discard prompt", async () => {
  const f = await editFixture();
  try {
    await f.field("#handoff-title", "Changed");
    await f.field("#handoff-title", "Alpha");
    assert.equal(f.beforeUnload(), false);
    assert.equal(f.canLeave(), true);
    await f.click(f.button("Cancel editing"));
    assert.equal(f.confirmations.length, 0);
    assert.ok(f.button("Edit handoff"));
  } finally { await f.close(); }
});

for (const message of ["handoff_not_found: design", "invalid_handoff: broken", "handoff_write_failed: permission denied"]) test(`Save failure keeps the complete draft: ${message}`, async () => {
  const f = await editFixture();
  try {
    f.api.saveLocalHandoff = async () => { throw new Error(message); };
    await f.field("#handoff-title", "Draft title");
    await f.field("#handoff-summary", "Draft summary");
    await f.field("#handoff-content", "Draft body");
    await f.click(f.button("Save changes"));
    assert.match(globalThis.document.querySelector('[role="alert"]').textContent, /Your draft has been kept/);
    assert.equal(globalThis.document.getElementById("handoff-title").value, "Draft title");
    assert.equal(globalThis.document.getElementById("handoff-summary").value, "Draft summary");
    assert.equal(globalThis.document.getElementById("handoff-content").value, "Draft body");
    assert.equal(f.beforeUnload(), true);
  } finally { await f.close(); }
});

test("Pending saves block duplicate submissions, switching, refresh, cancel and leaving", async () => {
  const f = await editFixture();
  const pending = deferred();
  let saves = 0;
  try {
    f.api.saveLocalHandoff = () => { saves++; return pending.promise; };
    await f.field("#handoff-title", "Pending draft");
    await f.toggleGroup(2);
    await f.submit();
    await f.submit();
    await f.click(f.button("Refresh"));
    await f.click(globalThis.document.querySelectorAll(".handoff-item")[1]);
    await f.click(f.button("Cancel editing"));
    assert.equal(saves, 1);
    assert.equal(f.button("Refresh").disabled, true);
    assert.equal(f.canLeave(), false);
    assert.equal(f.confirmations.length, 0);
    assert.equal(f.beforeUnload(), true);
    assert.equal(globalThis.document.getElementById("handoff-title").value, "Pending draft");
    await React.act(async () => pending.resolve({ ...f.entries[0], title: "Pending draft", content: "Saved body", revision: 4 }));
    assert.equal(globalThis.document.querySelector("form"), null);
    assert.equal(f.beforeUnload(), false);
  } finally { await f.close(); }
});

test("Revision conflict compares both complete versions, copies draft, and reloads only after confirmation", async () => {
  const f = await editFixture();
  try {
    const latest = { ...f.entries[0], revision: 4, title: "MCP title", summary: "MCP summary", content: "MCP body\n".repeat(200) };
    f.api.saveLocalHandoff = async () => { throw new Error("revision_conflict: expected revision 3, current revision 4"); };
    f.api.localHandoff = async () => latest;
    await f.field("#handoff-title", "My title");
    await f.field("#handoff-summary", "My summary");
    await f.field("#handoff-content", "My body\n".repeat(200));
    await f.click(f.button("Save changes"));
    assert.match(globalThis.document.querySelector('[role="alert"]').textContent, /newer revision/);
    await f.click(f.button("Compare versions"));
    let areas = globalThis.document.querySelectorAll(".handoff-comparison textarea");
    assert.equal(areas.length, 2);
    assert.equal(areas[0].readOnly && areas[1].readOnly, true);
    assert.match(areas[0].value, /Title: My title\nSummary: My summary\n\nMarkdown:\nMy body/);
    assert.match(areas[1].value, /Revision: 4.*Title: MCP title.*Summary: MCP summary.*MCP body/s);
    await f.click(f.button("Copy my changes"));
    assert.equal(f.copied[0], areas[0].value);
    assert.match(globalThis.document.querySelector('[role="status"]').textContent, /copied/);
    f.navigator.clipboard.writeText = async () => { throw new Error("denied"); };
    await f.click(f.button("Copy my changes"));
    assert.match(globalThis.document.body.textContent, /Could not copy your changes/);
    await f.click(f.button("Return to editing"));
    assert.equal(globalThis.document.getElementById("handoff-title").value, "My title");
    await f.click(f.button("Load latest version"));
    assert.equal(globalThis.document.getElementById("handoff-title").value, "My title");
    f.confirm(true);
    f.api.localHandoff = async () => { throw new Error("disk unavailable"); };
    await f.click(f.button("Load latest version"));
    assert.match(globalThis.document.body.textContent, /Could not load the latest version/);
    assert.equal(globalThis.document.getElementById("handoff-title").value, "My title");
    f.api.localHandoff = async () => ({ ...latest, revision: 5 });
    await f.click(f.button("Load latest version"));
    assert.equal(globalThis.document.getElementById("handoff-title").value, "MCP title");
    assert.equal(globalThis.document.getElementById("handoff-content").value, latest.content);
    assert.match(globalThis.document.querySelector(".handoff-metadata").textContent, /5/);
    assert.equal(f.beforeUnload(), false);
    await f.field("#handoff-title", "Rebased title");
    let revision;
    f.api.saveLocalHandoff = async (_id, title, summary, content, expected) => { revision = expected; return { ...latest, title, summary, content, revision: expected + 1 }; };
    await f.click(f.button("Save changes"));
    assert.equal(revision, 5);
    assert.equal(globalThis.document.querySelector("form"), null);
  } finally { await f.close(); }
});

test("Failed or pending comparison reads keep the draft and block abandoning it", async () => {
  const f = await editFixture();
  const pending = deferred();
  try {
    await f.field("#handoff-title", "Keep me");
    f.api.saveLocalHandoff = async () => { throw new Error("revision_conflict: changed"); };
    await f.click(f.button("Save changes"));
    f.api.localHandoff = () => pending.promise;
    await f.click(f.button("Compare versions"));
    assert.equal(f.canLeave(), false);
    assert.equal(globalThis.document.querySelector('textarea[aria-label="My changes"]').value.includes("Keep me"), true);
    await React.act(async () => pending.reject(new Error("latest deleted")));
    assert.match(globalThis.document.body.textContent, /Could not load the latest version/);
    assert.equal(globalThis.document.querySelector('textarea[aria-label="My changes"]').value.includes("Keep me"), true);
    await f.click(f.button("Return to editing"));
    assert.equal(globalThis.document.getElementById("handoff-title").value, "Keep me");
  } finally { await f.close(); }
});

test("A later save failure replaces an earlier conflict warning without losing the draft", async () => {
  const f = await editFixture();
  try {
    await f.field("#handoff-title", "Keep draft");
    f.api.saveLocalHandoff = async () => { throw new Error("revision_conflict: changed"); };
    await f.click(f.button("Save changes"));
    assert.ok(f.button("Compare versions"));
    f.api.saveLocalHandoff = async () => { throw new Error("handoff_not_found: design"); };
    await f.click(f.button("Save changes"));
    assert.equal(f.button("Compare versions"), undefined);
    assert.match(globalThis.document.querySelector('[role="alert"]').textContent, /Could not save this handoff/);
    assert.equal(globalThis.document.getElementById("handoff-title").value, "Keep draft");
  } finally { await f.close(); }
});

test("Actual App navigation respects the Handoffs leave guard", async () => {
  const f = await editFixture(true, "en", true);
  try {
    await f.field("#handoff-title", "Unsaved navigation draft");
    await f.click(f.button("Logs"));
    assert.ok(globalThis.document.getElementById("handoff-title"));
    assert.equal(f.confirmations.length, 1);
    f.confirm(true);
    await f.click(f.button("Logs"));
    assert.equal(globalThis.document.querySelector(".handoffs-page"), null);
    assert.match(globalThis.document.body.textContent, /Logs page/);
    assert.equal(f.beforeUnload(), false);
  } finally { await f.close(); }
});

test("Editing and conflict actions are translated to Chinese", async () => {
  const f = await editFixture(false, "zh-CN");
  try {
    await f.field("#handoff-title", "草稿");
    f.api.saveLocalHandoff = async () => { throw new Error("revision_conflict: changed"); };
    await f.click(f.button("保存更改"));
    assert.ok(f.button("查看版本对比"));
    assert.ok(f.button("复制我的修改"));
    assert.ok(f.button("加载最新版本"));
    await f.click(f.button("取消编辑并返回阅读"));
    assert.equal(f.confirmations[0], "放弃尚未保存的交接状态修改？");
    assert.equal(globalThis.document.getElementById("handoff-title").value, "草稿");
  } finally { await f.close(); }
});
