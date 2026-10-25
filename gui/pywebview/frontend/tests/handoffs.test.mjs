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
  const api = {
    localHandoffs: async () => ({ handoffs: [], issues: [] }),
    localHandoff: async (id) => {
      calls.push(id);
      return { ...entries.find((e) => e.id === id), content: "# Body\n\n**Read only**\n\n- First\n- Second" };
    },
  };
  const entries = [entry("a", "Same project", "Alpha"), entry("b", "Same project", "Beta"),
    entry("p", "Personal", "Private note", "personal")];
  const native = (tag) => (props) => React.createElement(tag, props, props.children);
  const mocks = {
    "@/lib/bridge": { coreApi: api, isWebUi: web, desktopApi: {}, errorMessage: (error) => error.message },
    "@/components/ui/button": { Button: native("button") },
    "@/components/ui/input": { Input: native("input") },
    "lucide-react": { RefreshCw: () => null },
    "react-markdown": { __esModule: true, default: Markdown },
  };
  function load(name) {
    if (mocks[name]) return mocks[name];
    if (!name.startsWith("@/")) return require(name);
    const extension = name === "@/lib/locale" ? "ts" : "tsx";
    const source = readFileSync(new URL(`../src/${name.slice(2)}.${extension}`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    const module = { exports: {} };
    vm.runInNewContext(code, { require: load, module, exports: module.exports, document: dom.window.document,
      window: dom.window, localStorage: dom.window.localStorage, setTimeout, clearTimeout });
    return module.exports;
  }
  const { Handoffs } = load("@/pages/handoffs");
  const render = () => React.act(async () => root.render(React.createElement(Handoffs)));
  const click = async (button) => { assert.ok(button); await React.act(async () => button.click()); };
  const search = async (value) => React.act(async () => {
    const input = globalThis.document.querySelector("input");
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  const close = async () => { await React.act(async () => root.unmount()); dom.window.close(); };
  return { api, entries, calls, render, click, search, close };
}

for (const web of [false, true]) test(`Handoffs group by identity and render selected metadata and Markdown (${web ? "HTTP" : "GUI"})`, async () => {
  const f = await fixture("en", web);
  try {
    f.api.localHandoffs = async () => ({ handoffs: f.entries, issues: [{ location: "broken.md", message: "invalid header" }] });
    await f.render();
    assert.equal(globalThis.document.querySelectorAll(".handoff-group").length, 3);
    assert.equal(globalThis.document.querySelector(".handoff-group h2").textContent, "Personal");
    assert.match(globalThis.document.querySelector("details").textContent, /broken.md.*invalid header/);
    await f.click(globalThis.document.querySelectorAll(".handoff-item")[2]);
    assert.deepEqual(f.calls, ["b:design"]);
    assert.match(globalThis.document.querySelector(".handoff-metadata").textContent, /Same project.*design.*Summary Beta.*3.*2026-10-25/s);
    assert.equal(globalThis.document.querySelector(".handoff-markdown h1").textContent, "Body");
    assert.equal(globalThis.document.querySelector(".handoff-markdown strong").textContent, "Read only");
    assert.equal(globalThis.document.querySelectorAll(".handoff-markdown li").length, 2);
    for (const query of ["alpha", "summary beta", "same project", "PRIVATE"]) {
      await f.search(query);
      assert.equal(globalThis.document.querySelectorAll(".handoff-item").length, query === "same project" ? 2 : 1);
    }
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
    await f.click(globalThis.document.querySelectorAll(".handoff-item")[1]);
    assert.match(globalThis.document.querySelector(".handoff-detail").textContent, /Loading handoff/);
    await f.click(globalThis.document.querySelectorAll(".handoff-item")[2]);
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
    await f.click(globalThis.document.querySelectorAll(".handoff-item")[1]);
    const preview = globalThis.document.querySelector(".handoff-markdown");
    assert.equal(preview.querySelector("script, iframe, img, [onerror]"), null);
    assert.deepEqual([...preview.querySelectorAll("a")].map((a) => a.getAttribute("href")), ["#body"]);
  } finally { await f.close(); }
});
