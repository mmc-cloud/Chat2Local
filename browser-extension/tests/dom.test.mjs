import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { page } from "./dom-helper.mjs";

test("semantic names: aria-labelledby, aria-label, label, placeholder and href are ANDed", async () => {
  const p = page('<span id="caption">Search docs</span><button aria-labelledby="caption">Icon</button>'
    + '<button aria-label="Save">Icon</button><label for="q">Query</label><input id="q" placeholder="Find">'
    + '<a href="/help">Help</a>');
  try {
    for (const target of [{ role: "button", name: "Search docs" }, { role: "button", aria_label: "Save" },
      { role: "textbox", label: "Query", placeholder: "Find" }, { role: "link", href: "https://test.example/help" }]) {
      const result = await p.call("inspect", { target });
      assert.equal(result.result.elements.length, 1);
    }
    assert.equal((await p.call("click", { target: { role: "button", name: "Query" } })).error.code, "target_not_found");
    assert.equal((await p.call("inspect", { target: { name: "Missing" } })).error.code, "target_not_found");
  } finally { p.dom.window.close(); }
});

test("click unique buttons and links, reject ambiguity without side effects", async () => {
  const p = page('<button id="one">Run</button><a href="#section">Next</a>' + '<button>Duplicate</button>'.repeat(7));
  let clicks = 0;
  p.document.addEventListener("click", (event) => { event.preventDefault(); clicks++; assert.equal(event.isTrusted, false); });
  try {
    const button = await p.call("click", { target: { role: "button", name: "Run" } });
    assert.equal(button.result.status, "clicked");
    const link = await p.call("click", { target: { role: "link", name: "Next" } });
    assert.equal(link.result.navigation_waited, false);
    const ambiguous = await p.call("click", { target: { name: "Duplicate" } });
    assert.equal(ambiguous.error.code, "ambiguous_target");
    assert.equal(ambiguous.error.details.candidates.length, 5);
    assert.equal(clicks, 2);
  } finally { p.dom.window.close(); }
});

test("href uses the resolved absolute URL, including the document base URL", async () => {
  const p = page('<base href="https://docs.example/guide/"><a href="../help">Help</a>');
  try {
    assert.equal((await p.call("inspect", { target: { href: "https://docs.example/help" } })).result.elements.length, 1);
    assert.equal((await p.call("inspect", { target: { href: "../help" } })).error.code, "target_not_found");
  } finally { p.window.close(); }
});

test("fill input and textarea through native setters and bubbling input/change events", async () => {
  const p = page('<input id="q" value="old"><textarea id="notes">old</textarea><input id="other" value="untouched">');
  const events = [];
  p.document.addEventListener("input", (e) => events.push([e.type, e.target.id, e.bubbles]));
  p.document.addEventListener("change", (e) => events.push([e.type, e.target.id, e.bubbles]));
  try {
    for (const id of ["q", "notes"]) {
      assert.equal((await p.call("fill", { target: { id }, value: "replacement 中文" })).result.status, "filled");
      assert.equal(p.document.getElementById(id).value, "replacement 中文");
    }
    assert.equal(p.document.getElementById("other").value, "untouched");
    assert.equal(events.length, 4);
    assert.ok(events.every((event) => event[2]));
    await p.call("fill", { target: { id: "q" }, value: "" });
    assert.equal(p.document.getElementById("q").value, "");
  } finally { p.dom.window.close(); }
});

for (const [html, code] of [
  ['<input id="x" hidden>', "target_hidden"], ['<div style="display:none"><input id="x"></div>', "target_hidden"],
  ['<fieldset disabled><input id="x"></fieldset>', "target_disabled"], ['<input id="x" readonly>', "target_readonly"],
  ['<input id="x" type="password" value="SECRET">', "protected_input"],
  ['<div contenteditable id="x">Text</div>', "unsupported_element"], ['<input id="x" type="file">', "unsupported_element"],
]) test(`reject protected/noninteractive control: ${code} ${html.slice(0, 25)}`, async () => {
  const p = page(html);
  try { assert.equal((await p.call("fill", { target: { id: "x" }, value: "new" })).error.code, code); }
  finally { p.dom.window.close(); }
});

test("revalidate element after authorization and synchronous focus handlers", async () => {
  let p;
  p = page('<input id="q"><button id="b">Run</button>', { authorize: async () => {
    p.document.querySelector("#b").remove(); return { ok: true };
  } });
  try { assert.equal((await p.call("click", { target: { id: "b" } })).error.code, "stale_target"); }
  finally { p.dom.window.close(); }
  const q = page('<input id="q">');
  q.document.querySelector("input").addEventListener("focus", (event) => { event.target.readOnly = true; });
  try { assert.equal((await q.call("fill", { target: { id: "q" }, value: "new" })).error.code, "target_readonly"); }
  finally { q.dom.window.close(); }
});

test("dynamic appearance, bounded waits, changed page, revoked permission and untrusted senders", async () => {
  const p = page("");
  try {
    const pending = p.call("click", { target: { role: "button", name: "Later" }, wait_ms: 200 });
    p.window.setTimeout(() => { p.document.body.innerHTML = '<button>Later</button>'; }, 15);
    assert.equal((await pending).result.status, "clicked");
    assert.equal((await p.call("click", { target: { name: "Missing" }, wait_ms: 10 })).error.code, "target_not_found");
    assert.equal((await p.call("inspect", {}, { expected_url: "https://different.example/" })).error.code, "page_changed");
    assert.equal((await p.call("inspect", {}, { expires_at: Date.now() - 1 })).error.code, "request_expired");
    assert.equal((await p.dispatch({ type: "chat2local.dom" }, { id: "untrusted" })).ignored, true);
  } finally { p.dom.window.close(); }
  const denied = page('<button>Go</button>', { authorize: async () => ({ ok: false }) });
  try { assert.equal((await denied.call("click", { target: { name: "Go" } })).error.code, "permission_denied"); }
  finally { denied.dom.window.close(); }
});

test("inspect is bounded and never returns field values", async () => {
  const p = page('<input type="password" value="NEVER-RETURN-THIS">' + '<button>Run</button>'.repeat(25));
  try {
    const result = await p.call("inspect", { limit: 3 });
    assert.equal(result.result.elements.length, 3);
    assert.equal(result.result.truncated, true);
    assert.equal(JSON.stringify(result).includes("NEVER-RETURN-THIS"), false);
  } finally { p.dom.window.close(); }
});

test("actual React 18 and Vue controlled inputs update their rendered state", async () => {
  const p = page('<div id="react"></div><div id="vue"></div>');
  try {
    p.window.eval(readFileSync(new URL("../node_modules/react/umd/react.development.js", import.meta.url), "utf8"));
    p.window.eval(readFileSync(new URL("../node_modules/react-dom/umd/react-dom.development.js", import.meta.url), "utf8"));
    const { React, ReactDOM } = p.window;
    function Controlled() {
      const [value, setValue] = React.useState("old");
      return React.createElement("div", null, React.createElement("input", { id: "react-input", value,
        onChange: (event) => setValue(event.target.value) }), React.createElement("output", { id: "react-output" }, value));
    }
    ReactDOM.flushSync(() => ReactDOM.createRoot(p.document.getElementById("react")).render(React.createElement(Controlled)));
    assert.equal((await p.call("fill", { target: { id: "react-input" }, value: "React state" })).result.status, "filled");
    await new Promise((resolve) => p.window.setTimeout(resolve, 0));
    assert.equal(p.document.getElementById("react-output").textContent, "React state");
    p.window.eval(readFileSync(new URL("../node_modules/vue/dist/vue.global.prod.js", import.meta.url), "utf8"));
    const { Vue } = p.window;
    Vue.createApp({ data: () => ({ value: "old" }), template: '<textarea id="vue-input" v-model="value"></textarea><output id="vue-output">{{ value }}</output>' }).mount("#vue");
    assert.equal((await p.call("fill", { target: { id: "vue-input" }, value: "Vue state" })).result.status, "filled");
    await Vue.nextTick();
    assert.equal(p.document.getElementById("vue-output").textContent, "Vue state");
  } finally { p.dom.window.close(); }
});
