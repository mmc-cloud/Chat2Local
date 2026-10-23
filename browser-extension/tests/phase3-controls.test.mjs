import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { browserPage } from "./operations-helper.mjs";

const tab = { tab_id: 1 };
const call = (p, operation, args = {}) => p.f.call(`browser.page.${operation}`, { tab, ...args });
const inspect = async (p, target, extra = {}) => (await call(p, "inspect", { target, ...extra })).result.elements[0];
const code = (response, expected) => assert.equal(response.error?.code, expected, JSON.stringify(response));
const plain = (value) => JSON.parse(JSON.stringify(value));

test("native select discovery pages all options, disabled optgroups and current selection without reading other values", async () => {
  const p = browserPage('<input value="PRIVATE-INPUT"><textarea>PRIVATE-TEXTAREA</textarea><select id="x"><option value="">Empty</option>'
    + '<optgroup label="Disabled" disabled><option value="b">Beta</option></optgroup>'
    + Array.from({ length: 73 }, (_, i) => `<option value="v${i}" ${i === 70 ? "selected" : ""}>Choice ${i}</option>`).join("") + '</select>');
  try {
    for (const field of p.document.querySelectorAll("input,textarea")) Object.defineProperty(field, "value", { get() { throw Error("must not read"); } });
    const first = await inspect(p, { id: "x" });
    assert.equal(first.select_options, undefined);
    assert.deepEqual(plain(first.state.selection), { multiple: false, total_options: 75, selected_count: 1, selected_indices: [72], has_more: false });
    const all = [];
    let offset = 0;
    do {
      const item = await inspect(p, { ref: first.ref }, { select_options: { offset, limit: 13 } });
      const page = item.select_options;
      assert.equal(page.total, 75); assert.equal(page.offset, offset); assert.ok(page.options.length <= 13);
      all.push(...page.options);
      assert.equal(page.has_more, page.next_offset !== null);
      assert.equal(page.truncated, page.has_more);
      offset = page.next_offset;
    } while (offset !== null);
    assert.deepEqual(all.map((option) => option.index), Array.from({ length: 75 }, (_, i) => i));
    assert.equal(all[0].value, ""); assert.equal(all[1].disabled, true); assert.equal(all[72].selected, true);
    assert.equal(JSON.stringify(all).includes("PRIVATE-"), false);
    code(await call(p, "select", { target: { ref: first.ref }, options: [{ index: 1 }] }), "option_disabled");
    assert.equal((await call(p, "select", { target: { ref: first.ref }, options: [{ index: 74 }] })).ok, true);
    assert.deepEqual(plain((await inspect(p, { ref: first.ref })).state.selection.selected_indices), [74]);
    const last = await inspect(p, { ref: first.ref }, { select_options: { offset: 9999 } });
    assert.equal(last.select_options.options.length, 0); assert.equal(last.select_options.has_more, false);
  } finally { p.window.close(); }
});

test("option output is redacted, byte bounded and continuable with long Unicode values", async () => {
  const p = browserPage('<select id="x" multiple></select>');
  const select = p.document.querySelector("select");
  try {
    for (let index = 0; index < 65; index++) {
      const option = p.document.createElement("option");
      option.textContent = index === 0 ? "token=PRIVATE-LABEL" : "好".repeat(256);
      option.value = index === 0 ? "https://example.com/path?token=PRIVATE-VALUE" : "好".repeat(256);
      option.selected = true; select.append(option);
    }
    const state = (await inspect(p, { id: "x" })).state.selection;
    assert.equal(state.selected_count, 65); assert.equal(state.selected_indices.length, 20); assert.equal(state.has_more, true);
    const indices = [];
    let offset = 0;
    do {
      const item = await inspect(p, { id: "x" }, { select_options: { offset, limit: 50 } });
      const page = item.select_options;
      assert.ok(Buffer.byteLength(JSON.stringify(page.options)) < 17 * 1024);
      assert.equal(JSON.stringify(page).includes("PRIVATE-"), false);
      indices.push(...page.options.map((option) => option.index));
      assert.ok(!page.has_more || page.next_offset > offset);
      offset = page.next_offset;
    } while (offset !== null);
    assert.equal(indices.length, 65);
  } finally { p.window.close(); }
});

test("option discovery rejects nonselect, ambiguous, stale and sensitive targets without expansion or value fallback", async () => {
  const p = browserPage('<input id="text"><select id="x"><option>A</option></select><select id="y"><option>B</option></select>'
    + '<select id="api-key"><option value="PRIVATE-VALUE">Private</option></select>');
  try {
    const args = { select_options: {} };
    code(await call(p, "inspect", { target: { id: "text" }, ...args }), "unsupported_element");
    code(await call(p, "inspect", { target: { css: "select" }, ...args }), "ambiguous_target");
    code(await call(p, "inspect", { target: { id: "api-key" }, ...args }), "protected_input");
    const ref = (await inspect(p, { id: "x" })).ref;
    p.document.querySelector("#x").replaceWith(p.document.querySelector("#x").cloneNode(true));
    code(await call(p, "inspect", { target: { ref }, ...args }), "stale_target");
    for (const bad of [{ select_options: {} }, { target: { id: "x" }, select_options: { offset: -1 } },
      { target: { id: "x" }, select_options: { limit: 51 } }, { target: { id: "x" }, select_options: { limit: true } },
      { target: { id: "x" }, select_options: { value: "PRIVATE" } }]) code(await call(p, "inspect", bad), "invalid_arguments");
  } finally { p.window.close(); }
});

test("option discovery retains credential-marker protection on option, optgroup and select ancestors", async () => {
  for (const html of ['<select id="x"><option id="token">PRIVATE-RAW</option></select>',
    '<select id="x"><optgroup id="api-key"><option>PRIVATE-RAW</option></optgroup></select>',
    '<section id="password"><select id="x"><option>PRIVATE-RAW</option></select></section>']) {
    const p = browserPage(html);
    try {
      const response = await call(p, "inspect", { target: { id: "x" }, select_options: {} });
      code(response, "protected_input"); assert.equal(JSON.stringify(response).includes("PRIVATE-RAW"), false);
    } finally { p.window.close(); }
  }
});

test("combobox portal workflow discovers relationships, expands, waits, reads options, selects and verifies state", async () => {
  const p = browserPage('<div id="app"><button id="combo" role="combobox" aria-label="Fruit" aria-expanded="false" aria-controls="choices" aria-owns="choices">Choose</button></div>'
    + '<div id="portal"></div>');
  const combo = p.document.querySelector("#combo");
  combo.addEventListener("click", () => {
    combo.setAttribute("aria-expanded", "true");
    p.window.setTimeout(() => {
      p.document.querySelector("#portal").innerHTML = '<div id="choices" role="listbox"><div role="option" aria-selected="false">Apple</div><div id="pear" role="option" aria-selected="false">Pear</div></div>';
      p.document.querySelector("#pear").addEventListener("click", (event) => {
        event.currentTarget.setAttribute("aria-selected", "true"); combo.setAttribute("aria-activedescendant", "pear");
        combo.setAttribute("aria-expanded", "false"); combo.textContent = "Pear";
      });
    }, 10);
  });
  try {
    const before = await inspect(p, { role: "combobox", name: "Fruit" });
    assert.equal(before.state.expanded, false); assert.equal(before.state.controls, "choices"); assert.equal(before.state.owns, "choices");
    assert.equal((await call(p, "click", { target: { ref: before.ref } })).ok, true);
    assert.equal((await call(p, "wait_for", { target: { role: "listbox", id: "choices" }, state: "visible", timeout_ms: 500 })).ok, true);
    const options = (await call(p, "dom.inspect", { css: '[role="option"]', scope: "#choices", depth: 0 })).result.elements;
    assert.equal(options.length, 2); assert.ok(options.every((option) => option.state.selected === false));
    assert.equal((await call(p, "click", { target: { ref: options.find((option) => option.text === "Pear").ref } })).ok, true);
    const after = await inspect(p, { ref: before.ref });
    assert.equal(after.state.expanded, false); assert.equal(after.state.active_descendant, "pear");
    assert.equal((await inspect(p, { id: "pear" })).state.selected, true);
    code(await call(p, "select", { target: { ref: before.ref }, options: [{ label: "Apple" }] }), "unsupported_element");
  } finally { p.window.close(); }
});

for (const widget of ["checkbox", "switch"]) test(`ARIA ${widget} is idempotent and verifies actual state with one click`, async () => {
  const p = browserPage(`<div id="x" role="${widget}" tabindex="0" aria-checked="false">Setting</div>`);
  const element = p.document.querySelector("#x");
  let clicks = 0;
  element.addEventListener("click", () => { clicks++; element.setAttribute("aria-checked", element.getAttribute("aria-checked") === "true" ? "false" : "true"); });
  try {
    const ref = (await inspect(p, { id: "x" })).ref;
    for (const checked of [false, true, true, false, false]) {
      assert.equal((await call(p, "set_checked", { target: { ref }, checked })).result.checked, checked);
      const item = await inspect(p, { ref });
      assert.equal(item.state.checked, checked); assert.equal(item.state.check_state, checked ? "checked" : "unchecked");
    }
    assert.equal(clicks, 2);
  } finally { p.window.close(); }
});

for (const mode of ["no-handler", "revert", "missing", "replace", "disable", "inert", "readonly", "ambiguous"]) {
  test(`ARIA checked ${mode} fails safely without repeating or choosing a substitute`, async () => {
    const p = browserPage('<div id="x" role="checkbox" tabindex="0" aria-checked="false">Check</div>');
    const element = p.document.querySelector("#x");
    let clicks = 0;
    element.addEventListener("click", () => {
      clicks++;
      if (mode === "replace") element.replaceWith(element.cloneNode(true));
      if (mode === "revert") { element.setAttribute("aria-checked", "true"); queueMicrotask(() => element.setAttribute("aria-checked", "false")); }
    });
    if (mode === "missing") element.removeAttribute("aria-checked");
    if (mode === "disable") element.setAttribute("aria-disabled", "true");
    if (mode === "inert") element.setAttribute("inert", "");
    if (mode === "readonly") element.setAttribute("aria-readonly", "true");
    if (mode === "ambiguous") element.after(element.cloneNode(true));
    try {
      code(await call(p, "set_checked", { target: { id: "x" }, checked: true }), { replace: "stale_target", disable: "target_disabled",
        inert: "target_disabled", readonly: "target_readonly", ambiguous: "ambiguous_target" }[mode] || "state_not_reached");
      assert.equal(clicks, ["no-handler", "revert", "replace"].includes(mode) ? 1 : 0);
      assert.equal(p.document.querySelector("#x").getAttribute("aria-checked"), mode === "missing" ? null : "false");
    } finally { p.window.close(); }
  });
}

for (const structure of ["for", "nested", "radio"]) test(`hidden native input activates only its ${structure} associated label`, async () => {
  const radio = structure === "radio";
  const p = browserPage(structure === "nested" ? '<label>Consent<input id="x" type="checkbox" hidden><span>Appearance</span></label>'
    : `<input id="x" type="${radio ? "radio" : "checkbox"}" hidden><label for="x">Consent</label>`);
  let inputClicks = 0, labelClicks = 0;
  p.document.querySelector("input").addEventListener("click", () => inputClicks++);
  p.document.querySelector("label").addEventListener("click", (event) => { if (event.target === event.currentTarget) labelClicks++; });
  try {
    assert.equal((await call(p, "set_checked", { target: { id: "x" }, checked: true })).ok, true);
    assert.equal((await inspect(p, { id: "x" })).state.check_state, "checked");
    assert.equal((await call(p, "set_checked", { target: { id: "x" }, checked: true })).result.changed, false);
    assert.equal(inputClicks, 1); assert.equal(labelClicks, 1);
    if (radio) code(await call(p, "set_checked", { target: { id: "x" }, checked: false }), "unsupported_state");
  } finally { p.window.close(); }
});

for (const problem of ["unrelated", "multiple-labels", "disabled-input", "disabled-label", "readonly-label", "reassociated", "replacement", "reveal-other", "hidden-duplicate"]) {
  test(`hidden checkbox ${problem} never activates a guessed appearance or different native input`, async () => {
    const p = browserPage('<input id="x" type="checkbox" hidden><input id="other" type="checkbox"><label for="x">Check</label><div role="checkbox" aria-checked="false">Appearance</div>');
    const input = p.document.querySelector("#x"), label = p.document.querySelector("label");
    let clicks = 0;
    p.document.addEventListener("click", () => clicks++);
    if (problem === "unrelated") label.htmlFor = "other";
    if (problem === "multiple-labels") label.after(label.cloneNode(true));
    if (problem === "disabled-input") input.disabled = true;
    if (problem === "disabled-label") label.setAttribute("inert", "");
    if (problem === "readonly-label") label.setAttribute("aria-readonly", "true");
    if (problem === "hidden-duplicate") input.after(input.cloneNode(true));
    label.scrollIntoView = () => {
      if (problem === "reassociated") label.htmlFor = "other";
      if (problem === "replacement") input.replaceWith(input.cloneNode(true));
      if (problem === "reveal-other") input.hidden = false;
    };
    try {
      const response = await call(p, "set_checked", { target: { id: "x" }, checked: true });
      code(response, { unrelated: "target_hidden", "multiple-labels": "ambiguous_target", "disabled-input": "target_disabled", "disabled-label": "target_disabled",
        "readonly-label": "target_readonly", reassociated: "stale_target", replacement: "stale_target", "reveal-other": "stale_target", "hidden-duplicate": "ambiguous_target" }[problem]);
      assert.equal(clicks, 0); assert.equal(p.document.querySelector("#other").checked, false);
    } finally { p.window.close(); }
  });
}

test("native and ARIA mixed states cannot masquerade as unchecked; at most one click attempts each transition", async () => {
  const p = browserPage('<input id="native" type="checkbox"><div id="aria" role="checkbox" aria-checked="mixed">Mixed</div>');
  const input = p.document.querySelector("input"), aria = p.document.querySelector("#aria");
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  aria.addEventListener("click", () => aria.setAttribute("aria-checked", "false"));
  try {
    input.indeterminate = true;
    for (const target of [{ id: "native" }, { id: "aria" }]) {
      assert.equal((await inspect(p, target)).state.check_state, "mixed");
      assert.equal((await call(p, "dom.inspect", { css: `#${target.id}`, depth: 0 })).result.elements[0].state.indeterminate, true);
    }
    code(await call(p, "set_checked", { target: { id: "native" }, checked: false }), "state_not_reached");
    assert.equal(input.checked, true); assert.equal(input.indeterminate, false); assert.equal(clicks, 1);
    assert.equal((await call(p, "set_checked", { target: { id: "aria" }, checked: false })).ok, true);
    assert.equal(clicks, 2);
    input.indeterminate = true;
    assert.equal((await call(p, "set_checked", { target: { id: "native" }, checked: false })).ok, true);
    assert.equal(input.checked, false); assert.equal(input.indeterminate, false); assert.equal(clicks, 3);
  } finally { p.window.close(); }
});

test("one click can await a delayed same-node ARIA update and keeps its pre-input result snapshot", async () => {
  const p = browserPage('<button id="x" role="checkbox" aria-checked="false">Before</button>');
  const element = p.document.querySelector("#x");
  let clicks = 0;
  element.addEventListener("click", () => {
    clicks++; element.textContent = "After";
    p.window.setTimeout(() => element.setAttribute("aria-checked", "true"), 70);
  });
  try {
    const response = await call(p, "set_checked", { target: { name: "Before" }, checked: true });
    assert.equal(response.ok, true, JSON.stringify(response)); assert.equal(clicks, 1);
    assert.equal(response.result.target.name, "Before"); assert.equal(element.textContent, "After");
    assert.equal((await inspect(p, { ref: response.result.target.ref })).state.checked, true);
  } finally { p.window.close(); }
});

test("permission revoked during ARIA state verification stops after one click and allows explicit later recovery", async () => {
  const p = browserPage('<button id="x" role="checkbox" aria-checked="false">Check</button>');
  let clicks = 0;
  p.document.querySelector("#x").addEventListener("click", (event) => {
    clicks++; event.currentTarget.setAttribute("aria-checked", "true"); p.f.settings.pageOrigins = [];
  });
  try {
    code(await call(p, "set_checked", { target: { id: "x" }, checked: true }), "permission_denied");
    assert.equal(clicks, 1);
    code(await call(p, "inspect", { target: { id: "x" } }), "permission_denied");
    p.f.settings.pageOrigins = ["https://test.example/*"];
    assert.equal((await inspect(p, { id: "x" })).state.checked, true);
    assert.equal((await call(p, "set_checked", { target: { id: "x" }, checked: true })).result.changed, false);
    assert.equal(clicks, 1);
  } finally { p.window.close(); }
});

test("duplicate native IDs cannot redirect hidden input activation through another control's label", async () => {
  const p = browserPage('<input id="same" type="checkbox"><input id="same" type="checkbox" hidden><label for="same">First only</label>');
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  try {
    const items = (await call(p, "inspect", { target: { id: "same" } })).result.elements;
    code(await call(p, "set_checked", { target: { ref: items[1].ref }, checked: true }), "target_hidden");
    assert.equal(clicks, 0); assert.ok([...p.document.querySelectorAll("input")].every((element) => !element.checked));
  } finally { p.window.close(); }
});

test("critical ARIA relationships and state survive many incidental attributes; arbitrary/private attributes are excluded", async () => {
  const p = browserPage('<div id="x" class="widget" role="combobox" type="custom" name="Search" aria-label="Search" placeholder="Choose" tabindex="0"'
    + ' aria-expanded="true" aria-controls="portal" aria-owns="portal" aria-activedescendant="chosen" aria-selected="true" aria-readonly="true"'
    + ' data-private="PRIVATE-VALUE" aria-description="PRIVATE-VALUE">Choice</div>');
  try {
    const normal = await inspect(p, { id: "x" });
    const dom = (await call(p, "dom.inspect", { css: "#x", depth: 0 })).result.elements[0];
    assert.deepEqual(plain(normal.state), plain(dom.state));
    for (const key of ["aria-expanded", "aria-controls", "aria-owns", "aria-activedescendant", "aria-selected", "aria-readonly"]) assert.ok(key in dom.attributes);
    assert.equal(dom.read_only, true); assert.equal(dom.truncated, false);
    assert.equal(JSON.stringify(dom).includes("PRIVATE-"), false);
  } finally { p.window.close(); }
});

test("actual React/Vue ARIA switches and hidden labels update models and preserve refs across reactive updates", async () => {
  const p = browserPage('<div id="react"></div><div id="vue"></div>');
  try {
    for (const file of ["react/umd/react.development.js", "react-dom/umd/react-dom.development.js", "vue/dist/vue.global.js"]) {
      p.window.eval(readFileSync(new URL(`../node_modules/${file}`, import.meta.url), "utf8"));
    }
    const { React, ReactDOM, Vue } = p.window;
    function Settings() {
      const [on, setOn] = React.useState(false), [native, setNative] = React.useState(false);
      return React.createElement("section", null,
        React.createElement("button", { id: "react-aria", role: "switch", "aria-checked": on, onClick: () => setOn(!on) }, "Power"),
        React.createElement("input", { id: "react-native", type: "checkbox", hidden: true, checked: native, onChange: (event) => setNative(event.target.checked) }),
        React.createElement("label", { htmlFor: "react-native" }, "Native"), React.createElement("output", { id: "react-out" }, `${on}:${native}`));
    }
    ReactDOM.flushSync(() => ReactDOM.createRoot(p.document.querySelector("#react")).render(React.createElement(Settings)));
    Vue.createApp({ data: () => ({ on: false, native: false }), template: '<button id="vue-aria" role="switch" :aria-checked="on" @click="on=!on">Power</button>'
      + '<input id="vue-native" type="checkbox" hidden v-model="native"><label for="vue-native">Native</label><output id="vue-out">{{on}}:{{native}}</output>' }).mount(p.document.querySelector("#vue"));
    for (const framework of ["react", "vue"]) {
      for (const control of ["aria", "native"]) {
        const ref = (await inspect(p, { id: `${framework}-${control}` })).ref;
        assert.equal((await call(p, "set_checked", { target: { ref }, checked: true })).ok, true);
        assert.equal((await call(p, "set_checked", { target: { ref }, checked: true })).result.changed, false);
        assert.equal((await inspect(p, { ref })).state.check_state, "checked");
      }
      assert.equal(p.document.querySelector(`#${framework}-out`).textContent, "true:true");
    }
  } finally { p.window.close(); }
});
