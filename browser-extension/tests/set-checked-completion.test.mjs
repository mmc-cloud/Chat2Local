import assert from "node:assert/strict";
import test from "node:test";
import { browserPage } from "./operations-helper.mjs";

const call = (p, checked = true) => p.f.call("browser.page.set_checked", { tab: { tab_id: 1 }, target: { id: "x" }, checked });
const html = (kind) => kind === "native" ? '<input id="x" type="checkbox">'
  : `<button id="x" role="${kind}" aria-checked="false">Check</button>`;
const reach = (element, kind) => { if (kind !== "native") element.setAttribute("aria-checked", "true"); };
const changeReadiness = (element, change) => {
  if (change === "hide") element.hidden = true;
  if (change === "disable") { element.disabled = true; element.setAttribute("aria-disabled", "true"); }
  if (change === "readonly") { element.readOnly = true; element.setAttribute("aria-readonly", "true"); }
};

for (const kind of ["checkbox", "switch", "native"]) for (const change of ["disable", "hide", "readonly"]) {
  test(`set_checked ${kind}: achieved state survives post-click ${change}`, async () => {
    const p = browserPage(html(kind)), element = p.document.querySelector("#x");
    let clicks = 0;
    element.addEventListener("click", () => { clicks++; reach(element, kind); changeReadiness(element, change); });
    try {
      const result = await call(p);
      assert.equal(result.ok, true, JSON.stringify(result));
      assert.equal(result.result.checked, true); assert.equal(result.result.changed, true);
      assert.equal(clicks, 1);
      assert.equal(kind === "native" ? element.checked : element.getAttribute("aria-checked") === "true", true);
    } finally { p.window.close(); }
  });
}

for (const change of ["disable", "hide", "readonly"]) test(`unmet ARIA state after ${change} reports state_not_reached with one click`, async () => {
  const p = browserPage(html("switch")), element = p.document.querySelector("#x");
  let clicks = 0;
  element.addEventListener("click", () => { clicks++; changeReadiness(element, change); });
  try {
    assert.equal((await call(p)).error?.code, "state_not_reached");
    assert.equal(clicks, 1); assert.equal(element.getAttribute("aria-checked"), "false");
  } finally { p.window.close(); }
});

for (const kind of ["native", "switch"]) for (const change of ["remove", "replace", "detach-reinsert", "type"]) {
  test(`set_checked ${kind}: reached state cannot hide post-click ${change}`, async () => {
    const p = browserPage(html(kind)), element = p.document.querySelector("#x");
    let clicks = 0;
    element.addEventListener("click", () => {
      clicks++;
      reach(element, kind);
      if (change === "remove") element.remove();
      if (change === "replace") element.replaceWith(element.cloneNode(true));
      if (change === "detach-reinsert") { element.remove(); p.document.body.append(element); }
      if (change === "type") { if (kind === "native") element.type = "radio"; else element.setAttribute("role", "radio"); }
    });
    try {
      assert.equal((await call(p)).error?.code, "stale_target");
      assert.equal(clicks, 1); assert.equal(p.f.calls.messages.length, 1);
    } finally { p.window.close(); }
  });
}

for (const change of ["hide", "disable", "readonly", "reassociate", "remove-label", "replace-input"]) {
  test(`hidden native input: post-click label ${change} preserves association policy`, async () => {
    const p = browserPage('<input id="x" type="checkbox" hidden><label for="x">Check</label><input id="other" type="checkbox">');
    const element = p.document.querySelector("#x"), label = p.document.querySelector("label");
    let inputClicks = 0, labelClicks = 0;
    label.addEventListener("click", () => labelClicks++);
    element.addEventListener("click", () => {
      inputClicks++;
      if (change === "reassociate") label.htmlFor = "other";
      else if (change === "remove-label") label.remove();
      else if (change === "replace-input") element.replaceWith(element.cloneNode(true));
      else { changeReadiness(element, change); changeReadiness(label, change); }
    });
    try {
      const result = await call(p);
      if (["hide", "disable", "readonly"].includes(change)) assert.equal(result.ok, true, JSON.stringify(result));
      else assert.equal(result.error?.code, "stale_target");
      assert.equal(inputClicks, 1); assert.equal(labelClicks, 1);
      assert.equal(p.document.querySelector("#other").checked, false);
    } finally { p.window.close(); }
  });
}

test("post-click successful hidden/disabled state still requires current permission", async () => {
  const p = browserPage(html("switch")), element = p.document.querySelector("#x");
  let clicks = 0;
  element.addEventListener("click", () => {
    clicks++; reach(element, "switch"); changeReadiness(element, "hide"); changeReadiness(element, "disable");
    p.f.settings.pageOrigins = [];
  });
  try {
    assert.equal((await call(p)).error?.code, "permission_denied");
    assert.equal(clicks, 1); assert.equal(element.getAttribute("aria-checked"), "true");
    assert.equal(p.f.calls.messages.length, 1);
  } finally { p.window.close(); }
});

for (const change of ["navigate", "selector", "expire"]) test(`successful click retains post-click ${change} checks`, async () => {
  const p = browserPage(html("switch")), element = p.document.querySelector("#x");
  const expires = Date.now() + 10000;
  let clicks = 0;
  element.addEventListener("click", () => {
    clicks++; reach(element, "switch");
    if (change === "navigate") { p.window.history.replaceState(null, "", "/new"); p.f.tabs[0].url = p.window.location.href; }
    if (change === "selector") p.f.tabs[0].title = "Changed";
    if (change === "expire") p.window.Date.now = () => expires;
  });
  try {
    const result = await p.f.call("browser.page.set_checked", {
      tab: { tab_id: 1, title: "Test" }, target: { id: "x" }, checked: true }, expires);
    assert.equal(result.error?.code, change === "expire" ? "request_expired" : "page_changed");
    assert.equal(clicks, 1);
  } finally { p.window.close(); }
});
