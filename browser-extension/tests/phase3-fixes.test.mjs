import assert from "node:assert/strict";
import test from "node:test";
import { browserPage } from "./operations-helper.mjs";

const tab = { tab_id: 1 };
const call = (p, operation, args = {}) => p.f.call(`browser.page.${operation}`, { tab, ...args });
const code = (result, expected) => assert.equal(result.error?.code, expected, JSON.stringify(result));
const refFor = async (p, id = "x") => (await call(p, "inspect", { target: { id } })).result.elements[0].ref;

for (const [operation, html, args] of [
  ["click", '<button id="x">Run</button>', {}], ["fill", '<input id="x">', { value: "text" }],
  ["focus", '<input id="x">', {}], ["double_click", '<button id="x">Run</button>', {}],
  ["hover", '<button id="x">Run</button>', {}], ["press_key", '<input id="x">', { key: "Escape" }],
  ["select", '<select id="x"><option>A</option><option>B</option></select>', { options: [{ index: 1 }] }],
  ["set_checked", '<input id="x" type="checkbox">', { checked: true }],
  ["scroll", '<div id="x" style="overflow-y:hidden"></div>', { container: "element", delta_y: 20 }],
  ["wait_for", '<button id="x">Run</button>', { state: "actionable", timeout_ms: 0 }],
]) test(`#1 ${operation}: refs survive unrelated DOM edits and focus status updates`, async () => {
  const p = browserPage(`${html}<p id="status">idle</p><div id="unrelated"><i>Other</i></div>`);
  const node = p.document.querySelector("#x");
  if (operation === "scroll") {
    for (const [key, value] of Object.entries({ clientHeight: 100, scrollHeight: 1000 })) Object.defineProperty(node, key, { value });
    node.scrollBy = ({ top }) => { node.scrollTop += top; };
  }
  node.addEventListener("focus", () => {
    p.document.querySelector("#status").textContent = "focused";
    node.setAttribute("data-focused", "true");
  });
  try {
    const ref = await refFor(p);
    p.document.querySelector("#unrelated").replaceChildren(p.document.createElement("span"));
    const result = await call(p, operation, { ...args, target: { ref } });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal((await refFor(p)), ref);
    // Both reading paths continue to return the same live node identity.
    assert.equal((await call(p, "dom.inspect", { css: "#x" })).result.elements[0].ref, ref);
    assert.equal((await p.read()).ok, true);
    if (operation === "fill") assert.equal(node.value, "text");
  } finally { p.window.close(); }
});

for (const change of ["remove", "replace", "ancestor-remove", "detach-reinsert", "adopt", "hide", "disable"]) {
  test(`#1 ref after ${change}: no other node is ever clicked`, async () => {
    const p = browserPage('<section><button id="x">Run</button></section><button id="other">Run</button>');
    let clicks = 0;
    p.document.addEventListener("click", () => clicks++);
    try {
      const node = p.document.querySelector("#x"), ref = await refFor(p);
      if (change === "remove") node.remove();
      if (change === "replace") node.replaceWith(node.cloneNode(true));
      if (change === "ancestor-remove") node.parentElement.remove();
      if (change === "detach-reinsert") { const parent = node.parentElement; parent.remove(); p.document.body.append(parent); }
      if (change === "adopt") p.document.implementation.createHTMLDocument().body.append(node);
      if (change === "hide") node.hidden = true;
      if (change === "disable") node.disabled = true;
      const result = await call(p, "click", { target: { ref, css: "button" } });
      code(result, change === "hide" ? "target_hidden" : change === "disable" ? "target_disabled" : "stale_target");
      assert.equal(clicks, 0);
    } finally { p.window.close(); }
  });
}

test("#1 ref alone pins identity; explicit text/CSS conditions are revalidated per request and preparation", async () => {
  const p = browserPage('<button id="x" class="old">Before</button>');
  try {
    const node = p.document.querySelector("#x"), ref = await refFor(p);
    node.textContent = "After"; node.className = "new";
    code(await call(p, "click", { target: { ref, text: "Before" } }), "target_not_found");
    code(await call(p, "click", { target: { ref, css: ".old" } }), "target_not_found");
    assert.equal((await call(p, "click", { target: { ref } })).ok, true);
    node.addEventListener("focus", () => { node.textContent = "Focus changed"; });
    node.blur();
    code(await call(p, "click", { target: { ref, text: "After" } }), "stale_target");
    assert.equal(p.document.activeElement, node);
  } finally { p.window.close(); }
});

for (const change of ["hide", "remove", "replace", "disable", "blur", "move"]) test(`#2 keyup follows focus after keydown ${change}`, async () => {
  const p = browserPage('<section id="panel"><input id="x"></section><button id="next">Next</button>');
  const events = [];
  p.document.addEventListener("keydown", (e) => events.push([e.type, e.target.id]));
  p.document.addEventListener("keyup", (e) => events.push([e.type, e.target.id || e.target.localName]));
  const node = p.document.querySelector("#x");
  node.addEventListener("keydown", () => {
    if (change === "hide") p.document.querySelector("#panel").hidden = true;
    if (change === "remove") node.remove();
    if (change === "replace") node.replaceWith(node.cloneNode(true));
    if (change === "disable") node.disabled = true;
    if (change === "blur") node.blur();
    if (change === "move") p.document.querySelector("#next").focus();
  });
  try {
    const result = await call(p, "press_key", { target: { ref: await refFor(p) }, key: change === "move" ? "ArrowDown" : "Escape" });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(events, [["keydown", "x"], ["keyup", change === "move" ? "next" : "body"]]);
    assert.equal(result.result.keyup_target.id, change === "move" ? "next" : "");
    assert.equal(p.f.calls.messages.length, 2); // inspect + one key request, no replay
  } finally { p.window.close(); }
});

for (const change of ["permission", "navigate", "expire", "password"]) test(`#2 interrupted pair on ${change} reports partial dispatch without retry`, async () => {
  const p = browserPage('<input id="x"><input id="secret" type="password">');
  let downs = 0, ups = 0;
  p.document.addEventListener("keyup", () => ups++);
  p.document.querySelector("#x").addEventListener("keydown", () => {
    downs++;
    if (change === "permission") p.f.settings.pageOrigins = [];
    if (change === "navigate") { p.window.history.replaceState(null, "", "/new"); p.f.tabs[0].url = p.window.location.href; }
    if (change === "expire") p.window.Date.now = () => Date.now() + 100000;
    if (change === "password") p.document.querySelector("#secret").focus();
  });
  try {
    const result = await call(p, "press_key", { target: { id: "x" }, key: "Escape" });
    code(result, { permission: "permission_denied", navigate: "page_changed", expire: "request_expired", password: "protected_input" }[change]);
    assert.deepEqual(JSON.parse(JSON.stringify(result.error.details.events_dispatched)), { keydown: true, keyup: false });
    assert.equal(downs, 1); assert.equal(ups, 0); assert.equal(p.f.calls.messages.length, 1);
  } finally { p.window.close(); }
});

for (const revoke of [false, true]) test(`#2 keyup removes its recipient; final permission revoked=${revoke}`, async () => {
  const p = browserPage('<input id="x">');
  let ups = 0;
  p.document.querySelector("#x").addEventListener("keyup", (event) => {
    ups++; event.target.remove(); if (revoke) p.f.settings.pageOrigins = [];
  });
  try {
    const result = await call(p, "press_key", { target: { id: "x" }, key: "Escape" });
    if (revoke) {
      code(result, "permission_denied");
      assert.deepEqual(JSON.parse(JSON.stringify(result.error.details.events_dispatched)), { keydown: true, keyup: true });
    } else assert.equal(result.ok, true);
    assert.equal(ups, 1);
  } finally { p.window.close(); }
});

for (const attribute of ["readonly", 'aria-readonly="true"']) test(`#3 ${attribute} accepts supported keys while fill remains protected`, async () => {
  const p = browserPage(`<input id="x" ${attribute} value="unchanged">`);
  const events = [];
  p.document.addEventListener("keydown", (event) => { events.push(event.key); event.preventDefault(); });
  try {
    const ref = await refFor(p);
    for (const key of ["Escape", "ArrowDown", "a", "Backspace"]) {
      assert.equal((await call(p, "press_key", { target: { ref }, key })).ok, true);
    }
    assert.deepEqual(events, ["Escape", "ArrowDown", "a", "Backspace"]);
    assert.equal(p.document.querySelector("#x").value, "unchanged");
    code(await call(p, "fill", { target: { ref }, value: "new" }), "target_readonly");
  } finally { p.window.close(); }
});

for (const locator of ["text", "name", "css", "ref+text"]) test(`#4 double_click pins original node after first click changes ${locator}`, async () => {
  const p = browserPage('<button id="x" class="before">Before</button><output></output>');
  const node = p.document.querySelector("#x"), events = [];
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click", "dblclick"]) node.addEventListener(type, e => {
    events.push([e.type, e.detail]); assert.equal(e.target, node); assert.equal(e.isTrusted, false);
    if (type === "click") { node.textContent = "After"; node.id = "renamed"; node.className = "after"; }
  });
  try {
    const ref = await refFor(p);
    const target = locator === "text" ? { text: "Before" } : locator === "name" ? { role: "button", name: "Before" }
      : locator === "css" ? { css: ".before" } : { ref, text: "Before" };
    assert.equal((await call(p, "double_click", { target })).ok, true);
    assert.equal(events.length, 11);
    assert.deepEqual(events.filter(([type]) => type === "click"), [["click", 1], ["click", 2]]);
    assert.deepEqual(events.at(-1), ["dblclick", 2]);
    assert.equal((await call(p, "inspect", { target: { ref } })).result.elements[0].id, "renamed");
  } finally { p.window.close(); }
});

for (const change of ["remove", "replace", "hide", "disable", "detach-reinsert"]) test(`#4 first-click ${change} stops second click/dblclick`, async () => {
  const p = browserPage('<button id="x">Run</button>');
  let clicks = 0, doubles = 0;
  const node = p.document.querySelector("#x");
  node.addEventListener("click", () => {
    clicks++;
    if (change === "remove") node.remove();
    if (change === "replace") node.replaceWith(node.cloneNode(true));
    if (change === "hide") node.hidden = true;
    if (change === "disable") node.disabled = true;
    if (change === "detach-reinsert") { node.remove(); p.document.body.append(node); }
  });
  node.addEventListener("dblclick", () => doubles++);
  try {
    code(await call(p, "double_click", { target: { id: "x" } }), change === "hide" ? "target_hidden" : change === "disable" ? "target_disabled" : "stale_target");
    assert.equal(clicks, 1); assert.equal(doubles, 0);
  } finally { p.window.close(); }
});

test("#4 cancelled pointerdown suppresses compatibility down/up, preserving pointerup/click/dblclick", async () => {
  const p = browserPage('<button id="x">Run</button>'), seen = [];
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click", "dblclick"]) p.document.addEventListener(type, e => {
    seen.push(e.type); if (type === "pointerdown") e.preventDefault();
  });
  try {
    assert.equal((await call(p, "double_click", { target: { id: "x" } })).ok, true);
    assert.deepEqual(seen, ["pointerdown", "pointerup", "click", "pointerdown", "pointerup", "click", "dblclick"]);
  } finally { p.window.close(); }
});

test("#5 enter is emitted once per ancestor outer-to-inner, over/move bubble from target only", async () => {
  const p = browserPage('<section id="outer"><div id="inner"><button id="x">Menu</button></div></section><aside id="sibling"></aside>');
  const order = [], own = [];
  for (const node of p.document.querySelectorAll("html,body,section,div,button,aside")) {
    for (const type of ["pointerenter", "mouseenter"]) node.addEventListener(type, e => {
      assert.equal(e.target, node); assert.equal(e.bubbles, false); assert.equal(e.composed, false);
      assert.equal(e.button, type === "pointerenter" ? -1 : 0); assert.equal(e.buttons, 0);
      assert.equal(e.relatedTarget, null); own.push([type, node.id || node.localName]);
    });
  }
  for (const type of ["pointerover", "pointerenter", "mouseover", "mouseenter", "pointermove", "mousemove"]) {
    p.document.addEventListener(type, e => order.push([type, e.target.id || e.target.localName]), true);
  }
  try {
    const result = await call(p, "hover", { target: { id: "x" } });
    assert.equal(result.ok, true);
    const path = ["html", "body", "outer", "inner", "x"];
    assert.deepEqual(own, ["pointerenter", "mouseenter"].flatMap(type => path.map(id => [type, id])));
    assert.deepEqual(order, [["pointerover", "x"], ...path.map(id => ["pointerenter", id]), ["mouseover", "x"],
      ...path.map(id => ["mouseenter", id]), ["pointermove", "x"], ["mousemove", "x"]]);
    assert.equal(p.document.activeElement, p.document.body);
    assert.equal(result.result.css_hover_guaranteed, false);
  } finally { p.window.close(); }
});

test("#5 reparenting during entry never sends enter to an obsolete ancestor", async () => {
  const p = browserPage('<section><button id="x">Menu</button></section><aside></aside>');
  let enters = 0;
  p.document.querySelector("#x").addEventListener("pointerover", e => p.document.querySelector("aside").append(e.target));
  p.document.querySelector("section").addEventListener("pointerenter", () => enters++);
  try { code(await call(p, "hover", { target: { id: "x" } }), "stale_target"); assert.equal(enters, 0); }
  finally { p.window.close(); }
});

for (const [overflow, size, accepted] of [["hidden", 1000, true], ["clip", 1000, false], ["hidden", 100, false]]) {
  test(`#12 explicit overflow:${overflow} dimension=${size}: actual movement or rejection, no focus/ancestor fallback`, async () => {
    const p = browserPage(`<section><div id="x" style="overflow-x:${overflow};overflow-y:${overflow}"></div></section>`);
    const node = p.document.querySelector("#x");
    let scrolled = 0;
    for (const [key, value] of Object.entries({ clientHeight: 100, scrollHeight: size, clientWidth: 100, scrollWidth: size })) Object.defineProperty(node, key, { value });
    node.scrollIntoView = node.focus = () => assert.fail("scroll must not prepare focus/scrollIntoView");
    node.parentElement.scrollBy = () => assert.fail("no ancestor fallback");
    node.scrollBy = ({ left, top }) => { scrolled++; node.scrollLeft = Math.min(900, node.scrollLeft + left); node.scrollTop = Math.min(900, node.scrollTop + top); };
    try {
      const result = await call(p, "scroll", { target: { id: "x" }, container: "element", delta_x: 20, delta_y: 1000 });
      if (!accepted) { code(result, "target_not_scrollable"); assert.equal(scrolled, 0); }
      else {
        assert.equal(result.result.before.x, 0); assert.equal(result.result.after.x, 20);
        assert.equal(result.result.after.y, 900); assert.equal(result.result.changed, true);
        assert.equal((await call(p, "scroll", { target: { id: "x" }, container: "element", delta_y: 1 })).result.changed, false);
      }
    } finally { p.window.close(); }
  });
}
