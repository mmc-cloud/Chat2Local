import assert from "node:assert/strict";
import test from "node:test";
import { page } from "./dom-helper.mjs";

const code = (response, expected) => assert.equal(response.error?.code, expected, JSON.stringify(response));
const rect = (left, top, right, bottom) => ({ left, top, right, bottom, width: right - left, height: bottom - top });
const wait = (p) => p.call("wait_for", { target: { id: "x" }, state: "actionable", timeout_ms: 0 });

for (const overflow of ["auto", "scroll", "hidden", "clip", "visible"]) {
  test(`observe ${overflow} scroll axes, range and edges without moving/focusing`, async () => {
    const p = page(`<div id="x" style="overflow-x:${overflow};overflow-y:${overflow}"><div>Content</div></div>`);
    const element = p.document.querySelector("#x");
    Object.defineProperties(element, { clientWidth: { value: 100 }, clientHeight: { value: 80 },
      scrollWidth: { value: 500 }, scrollHeight: { value: 400 } });
    let effects = 0;
    element.scrollBy = element.scrollIntoView = element.focus = () => effects++;
    try {
      const initial = (await p.call("inspect", { target: { id: "x" } })).result.elements[0].scroll;
      for (const axis of ["x", "y"]) {
        assert.equal(initial[axis].scrollable, ["auto", "scroll", "hidden"].includes(overflow));
        assert.equal(initial[axis].at_start, true); assert.equal(initial[axis].at_end, false);
      }
      element.scrollLeft = 400; element.scrollTop = 320;
      const final = (await p.call("dom.inspect", { css: "#x", depth: 0 })).result.elements[0].scroll;
      assert.equal(final.x.position, 400); assert.equal(final.x.min, 0); assert.equal(final.x.max, 400);
      assert.equal(final.y.position, 320); assert.equal(final.y.at_end, true); assert.equal(final.y.at_start, false);
      assert.equal(effects, 0);
    } finally { p.window.close(); }
  });
}

for (const scenario of ["self", "child", "center-covered", "offscreen", "zero", "pointer-none", "covered", "no-hit-api", "hidden", "inert", "fragment"]) {
  test(`actionable ${scenario} samples geometry/hits without side effects`, async () => {
    const p = page('<button id="x"><span>Run</span></button><div id="overlay">Cover</div>');
    const element = p.document.querySelector("#x"), overlay = p.document.querySelector("#overlay");
    Object.defineProperties(p.document.documentElement, { clientWidth: { value: 500 }, clientHeight: { value: 400 } });
    element.getClientRects = () => scenario === "offscreen" ? [rect(600, 100, 800, 200)] : scenario === "zero" ? [rect(10, 10, 10, 10)]
      : scenario === "fragment" ? [rect(600, 100, 800, 200), rect(-50, -30, 100, 30)] : [rect(10, 10, 110, 50)];
    const hits = [];
    p.document.elementFromPoint = (x, y) => {
      assert.ok(x >= 0 && x < 500 && y >= 0 && y < 400); hits.push([x, y]);
      if (scenario === "covered") return overlay;
      if (scenario === "center-covered" && x > 40 && x < 80) return overlay;
      return scenario === "child" ? element.firstElementChild : element;
    };
    if (scenario === "pointer-none") element.style.pointerEvents = "none";
    if (scenario === "hidden") element.hidden = true;
    if (scenario === "inert") element.setAttribute("inert", "");
    if (scenario === "no-hit-api") p.document.elementFromPoint = undefined;
    let effects = 0;
    element.scrollIntoView = element.focus = element.click = () => effects++;
    try {
      const result = await wait(p);
      if (["self", "child", "center-covered", "fragment"].includes(scenario)) assert.equal(result.ok, true, JSON.stringify(result));
      else code(result, "wait_timeout");
      if (scenario === "center-covered") assert.ok(hits.length > 1);
      assert.equal(effects, 0);
    } finally { p.window.close(); }
  });
}

test("actionable rechecks actual hit state after authorization and retains ref expiry distinction", async () => {
  let blocked = false;
  const p = page('<button id="x">Run</button><div id="cover"></div>', { authorize: async () => { blocked = true; return { ok: true }; } });
  p.document.elementFromPoint = () => p.document.querySelector(blocked ? "#cover" : "#x");
  try {
    code(await wait(p), "wait_timeout");
    const ref = (await p.call("inspect", { target: { id: "x" } })).result.elements[0].ref;
    p.document.querySelector("#x").replaceWith(p.document.querySelector("#x").cloneNode(true));
    code(await p.call("wait_for", { target: { ref }, state: "actionable", timeout_ms: 0 }), "stale_target");
  } finally { p.window.close(); }
});
