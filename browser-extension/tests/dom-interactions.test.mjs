import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { page } from "./dom-helper.mjs";

const call = (p, operation, extra = {}) => p.call(operation, { target: { id: "x" }, ...extra });
const code = (result, expected) => assert.equal(result.error?.code, expected, JSON.stringify(result));

test("double_click dispatches ordered mouse events, details and exactly two click activations", async () => {
  const p = page('<button id="x">Run</button><output id="out"></output>');
  const seen = [];
  for (const type of ["mousedown", "mouseup", "click", "dblclick"]) p.document.querySelector("button").addEventListener(type, (event) => {
    seen.push([event.type, event.detail, event.isTrusted, event.button]);
    if (type === "click") p.document.querySelector("output").textContent += "click";
  });
  try {
    const ref = (await call(p, "inspect")).result.elements[0].ref;
    const response = await call(p, "double_click", { target: { ref } });
    assert.equal(response.result.status, "double_clicked");
    assert.equal(response.result.event_is_trusted, false);
    assert.equal(response.result.navigation_waited, false);
    assert.deepEqual(seen, [["mousedown", 1, false, 0], ["mouseup", 1, false, 0], ["click", 1, false, 0],
      ["mousedown", 2, false, 0], ["mouseup", 2, false, 0], ["click", 2, false, 0], ["dblclick", 2, false, 0]]);
    code(await call(p, "click", { target: { ref } }), "stale_target");
  } finally { p.window.close(); }
});

test("hover dispatches pointer/mouse enter/over/move without focus or trusted/CSS hover promises", async () => {
  const p = page('<div><button id="x">Menu</button></div>');
  const seen = [];
  for (const type of ["pointerover", "pointerenter", "mouseover", "mouseenter", "pointermove", "mousemove"]) {
    p.document.querySelector("button").addEventListener(type, (event) => seen.push([type, event.bubbles, event.isTrusted]));
  }
  try {
    const result = await call(p, "hover");
    assert.equal(result.result.status, "hovered");
    assert.equal(result.result.css_hover_guaranteed, false);
    assert.equal(p.document.activeElement, p.document.body);
    assert.deepEqual(seen, [["pointerover", true, false], ["pointerenter", false, false], ["mouseover", true, false],
      ["mouseenter", false, false], ["pointermove", true, false], ["mousemove", true, false]]);
  } finally { p.window.close(); }
});

test("focus verifies activeElement without adding tabindex, and never pretends an unfocusable div focused", async () => {
  const p = page('<input id="x"><div id="plain">Plain</div>');
  try {
    assert.equal((await call(p, "focus")).result.status, "focused");
    assert.equal(p.document.activeElement.id, "x");
    code(await call(p, "focus", { target: { id: "plain" } }), "state_not_reached");
    assert.equal(p.document.querySelector("#plain").hasAttribute("tabindex"), false);
  } finally { p.window.close(); }
});

for (const key of ["Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End",
  "PageUp", "PageDown", "Backspace", "Delete", "Space", "a", "7"]) {
  test(`press_key sends ${key} with flags and cancellation without default typing/focus/submission`, async () => {
    const p = page('<form><input id="x" value="untouched"><button>Submit</button></form>');
    const events = [];
    let submits = 0;
    p.document.querySelector("form").addEventListener("submit", (event) => { event.preventDefault(); submits++; });
    for (const type of ["keydown", "keyup"]) p.document.querySelector("input").addEventListener(type, (event) => {
      events.push(event);
      if (type === "keydown") event.preventDefault();
    });
    try {
      const result = await call(p, "press_key", { key, modifiers: ["Control", "Shift", "Alt", "Meta"] });
      assert.equal(result.result.status, "key_pressed");
      assert.deepEqual(JSON.parse(JSON.stringify(result.result.default_prevented)), { keydown: true, keyup: false });
      assert.equal(events.length, 2);
      for (const event of events) {
        assert.equal(event.key, key === "Space" ? " " : key);
        assert.equal(event.code, key === "a" ? "KeyA" : key === "7" ? "Digit7" : key);
        assert.ok(event.ctrlKey && event.shiftKey && event.altKey && event.metaKey && event.bubbles && event.composed);
        assert.equal(event.isTrusted, false);
      }
      assert.equal(p.document.activeElement.id, "x");
      assert.equal(p.document.querySelector("input").value, "untouched");
      assert.equal(submits, 0);
    } finally { p.window.close(); }
  });
}

for (const [html, expected] of [['<input id="x" type="password">', "protected_input"],
  ['<input id="x" readonly>', "target_readonly"], ['<div id="x">Plain</div>', "state_not_reached"]]) {
  test(`press_key refuses ${expected}`, async () => {
    const p = page(html);
    try { code(await call(p, "press_key", { key: "Enter" }), expected); }
    finally { p.window.close(); }
  });
}

test("select replaces native single/multiple selections, supports empty values and exact normalized labels", async () => {
  const p = page('<select id="x"><option value="">Empty</option><option value="a"> First  choice </option><option value="b">Second</option></select>');
  const events = [];
  for (const type of ["input", "change"]) p.document.addEventListener(type, (event) => events.push([type, event.bubbles]));
  try {
    assert.equal((await call(p, "select", { options: [{ value: "a", label: "First choice", index: 1 }] })).result.changed, true);
    assert.equal(p.document.querySelector("select").value, "a");
    assert.equal((await call(p, "select", { options: [{ index: 1 }] })).result.changed, false);
    assert.equal((await call(p, "select", { options: [{ value: "" }] })).result.selected_count, 1);
    assert.deepEqual(events, [["input", true], ["change", true], ["input", true], ["change", true]]);
    p.document.querySelector("select").multiple = true;
    assert.equal((await call(p, "select", { options: [{ value: "b" }, { value: "a" }] })).result.selected_count, 2);
    assert.deepEqual(Array.from(p.document.querySelector("select").selectedOptions, (option) => option.value), ["a", "b"]);
    assert.equal((await call(p, "select", { options: [] })).result.selected_count, 0);
  } finally { p.window.close(); }
});

for (const [options, expected] of [[[{ value: "missing" }], "option_not_found"], [[{ value: "same" }], "ambiguous_option"],
  [[{ label: "Duplicate" }], "ambiguous_option"], [[{ index: 3 }], "option_disabled"], [[{ index: 4 }], "option_disabled"],
  [[], "invalid_arguments"], [[{ index: 0 }, { index: 0 }], "invalid_arguments"]]) {
  test(`select rejects ${JSON.stringify(options)}: ${expected} before changing selection/events`, async () => {
    const p = page('<select id="x"><option value="initial">Initial</option><option value="same">Duplicate</option>'
      + '<option value="same">Duplicate</option><option disabled>Disabled</option><optgroup disabled><option>Group disabled</option></optgroup></select>');
    let effects = 0;
    p.document.addEventListener("input", () => effects++);
    p.document.addEventListener("change", () => effects++);
    try {
      code(await call(p, "select", { options }), expected);
      assert.equal(p.document.querySelector("select").selectedIndex, 0);
      assert.equal(effects, 0);
    } finally { p.window.close(); }
  });
}

test("select resolves duplicates only by all conditions and rejects duplicate selections or prepared option replacement", async () => {
  const p = page('<select id="x" multiple><option value="same">One</option><option value="same">Two</option></select>');
  try {
    assert.equal((await call(p, "select", { options: [{ value: "same", label: "Two" }] })).result.selected_count, 1);
    code(await call(p, "select", { options: [{ value: "same", label: "Two" }, { index: 1 }] }), "invalid_arguments");
    p.document.querySelector("select").scrollIntoView = () => {
      const option = p.document.querySelector("option"); option.replaceWith(option.cloneNode(true));
    };
    code(await call(p, "select", { options: [{ index: 0 }] }), "stale_target");
  } finally { p.window.close(); }
});

test("set_checked is idempotent, respects cancelled activation and radio groups", async () => {
  const p = page('<input id="x" type="checkbox"><input id="r1" type="radio" name="r" checked><input id="r2" type="radio" name="r">');
  let clicks = 0, changes = 0;
  p.document.querySelector("#x").addEventListener("click", () => clicks++);
  p.document.querySelector("#x").addEventListener("change", () => changes++);
  try {
    for (const checked of [true, true, false, false]) {
      const result = await call(p, "set_checked", { checked });
      assert.equal(result.result.checked, checked);
    }
    assert.equal(clicks, 2); assert.equal(changes, 2);
    p.document.querySelector("#x").addEventListener("click", (event) => event.preventDefault());
    code(await call(p, "set_checked", { checked: true }), "state_not_reached");
    assert.equal(clicks, 3);
    code(await call(p, "set_checked", { target: { id: "r1" }, checked: false }), "unsupported_state");
    assert.equal((await call(p, "set_checked", { target: { id: "r2" }, checked: false })).result.changed, false);
    assert.equal((await call(p, "set_checked", { target: { id: "r2" }, checked: true })).result.checked, true);
    assert.equal(p.document.querySelector("#r1").checked, false);
    assert.equal(p.document.querySelector("#r2").checked, true);
  } finally { p.window.close(); }
});

test("state setters never claim success after page handlers revert checked or selection", async () => {
  const p = page('<select id="s"><option>A</option><option>B</option></select><input id="x" type="checkbox">');
  p.document.querySelector("#s").addEventListener("change", (event) => { event.target.selectedIndex = 0; });
  p.document.querySelector("#x").addEventListener("change", (event) => { event.target.checked = false; });
  try {
    code(await call(p, "select", { target: { id: "s" }, options: [{ index: 1 }] }), "state_not_reached");
    code(await call(p, "set_checked", { checked: true }), "state_not_reached");
  } finally { p.window.close(); }
});

test("select bounds native option counts and label reads before any selection or preparation", async () => {
  const p = page('<select id="x"><option>Initial</option><option label="' + " ".repeat(65537) + '">Long</option></select>');
  const element = p.document.querySelector("select");
  element.scrollIntoView = () => assert.fail("failed validation must precede scroll preparation");
  try {
    code(await call(p, "select", { options: [{ label: "Long" }] }), "page_too_large");
    element.options.length = 10001;
    code(await call(p, "select", { options: [{ index: 0 }] }), "page_too_large");
    assert.equal(element.selectedIndex, 0);
  } finally { p.window.close(); }
});

test("set_checked checks state again after focus and does not click if a handler already satisfied it", async () => {
  const p = page('<input id="x" type="checkbox">');
  const input = p.document.querySelector("input");
  let clicks = 0;
  input.addEventListener("click", () => clicks++);
  input.addEventListener("focus", () => { input.checked = true; });
  try {
    const result = await call(p, "set_checked", { checked: true });
    assert.equal(result.result.checked, true); assert.equal(result.result.changed, false);
    assert.equal(clicks, 0);
  } finally { p.window.close(); }
});

test("React/Vue synthetic mouse, keyboard and focus handlers observe the new event operations", async () => {
  const p = page('<div id="react"></div><div id="vue"></div>');
  try {
    p.window.eval(readFileSync(new URL("../node_modules/react/umd/react.development.js", import.meta.url), "utf8"));
    p.window.eval(readFileSync(new URL("../node_modules/react-dom/umd/react-dom.development.js", import.meta.url), "utf8"));
    const { React, ReactDOM } = p.window;
    function Events() {
      const [last, setLast] = React.useState("idle");
      return React.createElement("section", null, React.createElement("button", { id: "react-events",
        onDoubleClick: () => setLast("double"), onMouseEnter: () => setLast("hover"),
        onFocus: () => setLast("focus"), onKeyDown: (event) => setLast(event.key) }), React.createElement("output", { id: "react-event-out" }, last));
    }
    ReactDOM.flushSync(() => ReactDOM.createRoot(p.document.querySelector("#react")).render(React.createElement(Events)));
    p.window.eval(readFileSync(new URL("../node_modules/vue/dist/vue.global.prod.js", import.meta.url), "utf8"));
    const { Vue } = p.window;
    Vue.createApp({ data: () => ({ last: "idle" }),
      template: '<button id="vue-events" @dblclick="last=\'double\'" @mouseenter="last=\'hover\'" @focus="last=\'focus\'" @keydown="last=$event.key">Events</button><output id="vue-event-out">{{last}}</output>' }).mount("#vue");
    for (const framework of ["react", "vue"]) for (const [operation, expected, args] of [
      ["focus", "focus", {}], ["hover", "hover", {}], ["double_click", "double", {}], ["press_key", "Escape", { key: "Escape" }],
    ]) {
      assert.equal((await call(p, operation, { target: { id: `${framework}-events` }, ...args })).ok, true);
      await Vue.nextTick();
      await new Promise((resolve) => p.window.setTimeout(resolve, 0));
      assert.equal(p.document.querySelector(`#${framework}-event-out`).textContent, expected);
    }
  } finally { p.window.close(); }
});

test("native select and checked updates reach actual React 18 and Vue 3 component models", async () => {
  const p = page('<div id="react"></div><div id="vue"></div>');
  try {
    p.window.eval(readFileSync(new URL("../node_modules/react/umd/react.development.js", import.meta.url), "utf8"));
    p.window.eval(readFileSync(new URL("../node_modules/react-dom/umd/react-dom.development.js", import.meta.url), "utf8"));
    const { React, ReactDOM } = p.window;
    function Controls() {
      const [checked, setChecked] = React.useState(false), [value, setValue] = React.useState("a");
      return React.createElement("section", null,
        React.createElement("input", { id: "react-check", type: "checkbox", checked, onChange: (event) => setChecked(event.target.checked) }),
        React.createElement("select", { id: "react-select", value, onChange: (event) => setValue(event.target.value) },
          ...["a", "b"].map((item) => React.createElement("option", { key: item, value: item }, item))),
        React.createElement("output", { id: "react-out" }, `${checked}:${value}`));
    }
    ReactDOM.flushSync(() => ReactDOM.createRoot(p.document.querySelector("#react")).render(React.createElement(Controls)));
    p.window.eval(readFileSync(new URL("../node_modules/vue/dist/vue.global.prod.js", import.meta.url), "utf8"));
    const { Vue } = p.window;
    Vue.createApp({ data: () => ({ checked: false, value: ["a"] }),
      template: '<input id="vue-check" type="checkbox" v-model="checked"><select id="vue-select" multiple v-model="value"><option value="a">a</option><option value="b">b</option></select><output id="vue-out">{{checked}}:{{value.join(",")}}</output>' }).mount("#vue");
    for (const framework of ["react", "vue"]) {
      assert.equal((await call(p, "set_checked", { target: { id: `${framework}-check` }, checked: true })).ok, true);
      assert.equal((await call(p, "select", { target: { id: `${framework}-select` }, options: [{ value: "b" }] })).ok, true);
      await Vue.nextTick();
      await new Promise((resolve) => p.window.setTimeout(resolve, 0));
      assert.equal(p.document.querySelector(`#${framework}-out`).textContent, "true:b");
    }
  } finally { p.window.close(); }
});

function scrollable(element, { x = false, y = true } = {}) {
  element.style.overflowX = x ? "auto" : "hidden";
  element.style.overflowY = y ? "auto" : "hidden";
  for (const [key, value] of Object.entries({ clientWidth: 100, scrollWidth: x ? 1000 : 100, clientHeight: 100, scrollHeight: y ? 1000 : 100 })) {
    Object.defineProperty(element, key, { configurable: true, value });
  }
  element.scrollBy = ({ left, top, behavior }) => {
    assert.equal(behavior, "instant");
    element.scrollLeft = Math.min(900, Math.max(0, element.scrollLeft + left));
    element.scrollTop = Math.min(900, Math.max(0, element.scrollTop + top));
  };
}

test("scroll uses only the explicit page/element with relative pixels, clamps and reports actual movement", async () => {
  const p = page('<div id="outer"><div id="x"></div></div>');
  const element = p.document.querySelector("#x"), outer = p.document.querySelector("#outer");
  scrollable(element); scrollable(outer);
  Object.defineProperty(p.document, "scrollingElement", { value: p.document.documentElement });
  scrollable(p.document.scrollingElement, { x: true });
  element.scrollIntoView = () => assert.fail("explicit scrolling must not scrollIntoView");
  element.focus = () => assert.fail("scroll must not focus");
  try {
    const first = await call(p, "scroll", { container: "element", delta_y: 250 });
    assert.equal(first.result.after.y, 250); assert.equal(first.result.before.y, 0);
    assert.equal(element.scrollTop, 250); assert.equal(outer.scrollTop, 0);
    const bottom = await call(p, "scroll", { container: "element", delta_y: 100000 });
    assert.equal(bottom.result.after.y, 900);
    assert.equal((await call(p, "scroll", { container: "element", delta_y: 1 })).result.changed, false);
    assert.equal((await call(p, "scroll", { container: "element", delta_y: -100 })).result.after.y, 800);
    code(await call(p, "scroll", { container: "element", delta_x: 1 }), "target_not_scrollable");
    const viewport = await p.call("scroll", { container: "page", delta_y: 200, delta_x: 100 });
    assert.equal(viewport.result.target, null);
    assert.equal(viewport.result.after.x, 100); assert.equal(viewport.result.after.y, 200);
    assert.equal(outer.scrollTop, 0); assert.equal(element.scrollTop, 800);
  } finally { p.window.close(); }
});

const actions = [
  ["double_click", '<button id="x">Run</button>', {}], ["hover", '<button id="x">Run</button>', {}],
  ["focus", '<input id="x">', {}], ["press_key", '<input id="x">', { key: "Enter" }],
  ["select", '<select id="x"><option>A</option><option>B</option></select>', { options: [{ index: 1 }] }],
  ["set_checked", '<input id="x" type="checkbox">', { checked: true }],
  ["scroll", '<div id="x"></div>', { container: "element", delta_y: 1 }],
];
for (const [operation, html, args] of actions) {
  for (const problem of ["hidden", "disabled", "ambiguous", "stale"]) test(`${operation} shares the ${problem} target guard`, async () => {
    const p = page(html);
    const original = p.document.querySelector("#x");
    if (operation === "scroll") scrollable(original);
    let target = { id: "x" };
    if (problem === "hidden") original.hidden = true;
    if (problem === "disabled") original.classList.add("disable");
    if (problem === "ambiguous") original.after(original.cloneNode(true));
    if (problem === "stale") {
      target = { ref: (await call(p, "inspect")).result.elements[0].ref };
      original.replaceWith(original.cloneNode(true));
    }
    try { code(await call(p, operation, { ...args, target }), { hidden: "target_hidden", disabled: "target_disabled", ambiguous: "ambiguous_target", stale: "stale_target" }[problem]); }
    finally { p.window.close(); }
  });
}

for (const operation of ["double_click", "hover", "press_key"]) {
  for (const change of ["replace", "disable", "navigate", "expire"]) test(`${operation} stops a partial event sequence on ${change}`, async () => {
    const p = page('<button id="x">Run</button>');
    let events = 0;
    const expiresAt = Date.now() + 10000;
    const first = { double_click: "mousedown", hover: "pointerover", press_key: "keydown" }[operation];
    p.document.querySelector("#x").addEventListener(first, (event) => {
      events++;
      if (change === "replace") event.target.replaceWith(event.target.cloneNode(true));
      if (change === "disable") event.target.disabled = true;
      if (change === "navigate") p.window.history.replaceState(null, "", "/elsewhere");
      if (change === "expire") p.window.Date.now = () => expiresAt;
    });
    p.document.addEventListener(operation === "double_click" ? "click" : operation === "hover" ? "mouseover" : "keyup", () => events++);
    try {
      const result = await p.call(operation, { target: { id: "x" }, key: "Enter" }, { expires_at: expiresAt });
      code(result, { replace: "stale_target", disable: "target_disabled", navigate: "page_changed", expire: "request_expired" }[change]);
      assert.equal(events, 1);
    } finally { p.window.close(); }
  });
}
