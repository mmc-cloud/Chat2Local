import assert from "node:assert/strict";
import test from "node:test";
import { page } from "./dom-helper.mjs";
import { browserPage } from "./operations-helper.mjs";

const wait = (p, state, args = {}, extra = {}) => p.call("wait_for", { target: { id: "x" }, state, timeout_ms: 200, ...args }, extra);
const code = (result, expected) => assert.equal(result.error?.code, expected, JSON.stringify(result));

for (const state of ["attached", "detached", "visible", "hidden", "enabled", "disabled", "actionable"]) {
  test(`wait_for ${state} observes dynamic DOM/state without scroll, focus or events`, async () => {
    const absent = ["attached", "visible", "actionable"].includes(state);
    const p = page(absent ? "" : `<button id="x" ${state === "enabled" ? "disabled" : ""}>Later</button>`);
    let sideEffects = 0;
    p.window.HTMLElement.prototype.scrollIntoView = () => sideEffects++;
    p.window.HTMLElement.prototype.focus = () => sideEffects++;
    for (const type of ["click", "input", "change"]) p.document.addEventListener(type, () => sideEffects++);
    try {
      p.window.setTimeout(() => {
        if (absent) p.document.body.innerHTML = '<button id="x">Later</button>';
        const element = p.document.querySelector("#x");
        if (state === "detached") element.remove();
        if (state === "hidden") element.hidden = true;
        if (state === "enabled") element.disabled = false;
        if (state === "disabled") element.classList.add("disable");
      }, 10);
      const response = await wait(p, state);
      assert.equal(response.result.status, "matched"); assert.equal(response.result.state, state);
      assert.ok(response.result.elapsed_ms >= 0);
      assert.equal(response.result.target === null, state === "detached");
      if (response.result.target) assert.match(response.result.target.ref, /^el_/);
      assert.equal(sideEffects, 0);
    } finally { p.window.close(); }
  });
}

test("hidden/detached allow absence, enabled/disabled require existence regardless of visibility", async () => {
  const p = page("");
  try {
    for (const state of ["hidden", "detached"]) assert.equal((await wait(p, state, { timeout_ms: 0 })).result.target, null);
    for (const state of ["attached", "visible", "enabled", "disabled", "actionable"]) code(await wait(p, state, { timeout_ms: 0 }), "wait_timeout");
    p.document.body.innerHTML = '<button id="x" hidden disabled>Hidden disabled</button>';
    for (const state of ["attached", "hidden", "disabled"]) assert.equal((await wait(p, state, { timeout_ms: 0 })).result.status, "matched");
    code(await wait(p, "visible", { timeout_ms: 10 }), "wait_timeout");
    code(await wait(p, "enabled", { timeout_ms: 10 }), "wait_timeout");
    p.document.querySelector("button").disabled = false;
    assert.equal((await wait(p, "enabled", { timeout_ms: 0 })).result.status, "matched");
  } finally { p.window.close(); }
});

for (const state of ["attached", "detached", "visible", "hidden", "enabled", "disabled", "actionable"]) {
  test(`wait_for ${state} rejects multiple targets including hidden matches`, async () => {
    const p = page('<button id="x">Same</button><button id="x" hidden>Same</button>');
    try {
      const result = await wait(p, state, { timeout_ms: 0 });
      code(result, "ambiguous_target");
      assert.equal(result.error.details.candidates.length, 2);
    } finally { p.window.close(); }
  });
}

for (const state of ["detached", "hidden", "visible", "enabled", "disabled", "actionable"]) {
  test(`wait_for ${state} never interprets ref invalidation as a matching state`, async () => {
    const p = page('<button id="x" hidden disabled>Later</button>');
    const ref = (await p.call("inspect", { target: { id: "x" } })).result.elements[0].ref;
    try {
      // Invalidate even for states already satisfied; no negative success.
      p.document.querySelector("#x").remove();
      code(await wait(p, state, { target: { ref }, timeout_ms: 0 }), "stale_target");
    } finally { p.window.close(); }
  });
}

for (const [state, html, change] of [
  ["detached", '<button id="x">Later</button>', (element) => element.remove()],
  ["hidden", '<button id="x">Later</button>', (element) => { element.hidden = true; }],
  ["visible", '<button id="x" hidden>Later</button>', (element) => { element.hidden = false; }],
  ["enabled", '<button id="x" disabled>Later</button>', (element) => { element.disabled = false; }],
  ["actionable", '<button id="x" hidden>Later</button>', (element) => { element.hidden = false; }],
]) test(`ref becoming stale during a pending ${state} wait fails distinctly`, async () => {
  const p = page(html);
  const ref = (await p.call("inspect", { target: { id: "x" } })).result.elements[0].ref;
  try {
    const pending = wait(p, state, { target: { ref } });
    p.window.setTimeout(() => change(p.document.querySelector("#x")), 10);
    code(await pending, "stale_target");
  } finally { p.window.close(); }
});

// Only the isolated page clock advances; these deadline cases do not sleep or
// depend on scheduler speed. Async authorizations are controlled mutation points.
function timedPage(html, authorize) {
  let now = 1000, calls = 0, p;
  const advance = (milliseconds) => { now += milliseconds; };
  p = page(html, { authorize: async () => (await authorize({ p, call: ++calls, now, advance })) ?? { ok: true } });
  p.window.Date.now = () => now;
  return { p, advance, calls: () => calls,
    wait: (timeout, extra = {}, message = {}) => wait(p, "visible", { timeout_ms: timeout, ...extra }, { expires_at: 10000, ...message }) };
}

for (const [timeout, latency, expected] of [[0, 0, "wait_timeout"], [0, 180, "wait_timeout"],
  [25, 24, null], [25, 25, "wait_timeout"], [25, 26, "wait_timeout"]]) {
  test(`initial consent cannot qualify a late new match: timeout=${timeout}, latency=${latency}`, async () => {
    const t = timedPage("", ({ p, call, advance }) => {
      if (call === 1) { advance(latency); p.document.body.innerHTML = '<button id="x">Later</button>'; }
    });
    try {
      const result = await t.wait(timeout);
      if (expected) code(result, expected);
      else assert.equal(result.result.status, "matched");
      assert.equal(t.calls(), 2); // Initial and final authorization are retained.
    } finally { t.p.window.close(); }
  });
}

test("timeout=0 never accepts appearance during final authorization either", async () => {
  const t = timedPage("", ({ p, call, advance }) => {
    if (call === 2) { advance(180); p.document.body.innerHTML = '<button id="x">Too late</button>'; }
  });
  try { code(await t.wait(0), "wait_timeout"); assert.equal(t.calls(), 2); }
  finally { t.p.window.close(); }
});

test("an immediate match lost during initial confirmation cannot requalify later", async () => {
  const t = timedPage('<button id="x">Ready</button>', ({ p, call, advance }) => {
    advance(90);
    p.document.querySelector("#x").hidden = call === 1;
  });
  try { code(await t.wait(0), "wait_timeout"); }
  finally { t.p.window.close(); }
});

for (const [latency, expected] of [[49, null], [50, "wait_timeout"], [51, "wait_timeout"]]) {
  test(`periodic authorization cannot qualify a new match past deadline: latency=${latency}`, async () => {
    const t = timedPage("", ({ p, call, advance }) => {
      if (call === 2) { advance(latency); p.document.body.innerHTML = '<button id="x">Periodic appearance</button>'; }
    });
    t.p.window.setTimeout = (callback) => { t.advance(250); queueMicrotask(callback); return 1; };
    try {
      const result = await t.wait(300);
      if (expected) code(result, expected);
      else assert.equal(result.result.status, "matched");
      assert.equal(t.calls(), 2);
    } finally { t.p.window.close(); }
  });
}

for (const timeout of [0, 20]) test(`already qualified match remains valid through slow authorization: timeout=${timeout}`, async () => {
  const t = timedPage('<button id="x">Ready</button>', ({ advance }) => advance(90));
  try {
    const result = await t.wait(timeout);
    assert.equal(result.result.status, "matched"); assert.equal(result.result.elapsed_ms, 180);
    assert.equal(t.calls(), 2);
  } finally { t.p.window.close(); }
});

for (const change of ["hide", "replace"]) test(`late final confirmation cannot reuse a qualified match after ${change}`, async () => {
  const t = timedPage('<button id="x">Ready</button>', ({ p, call, advance }) => {
    if (call === 2) {
      advance(25);
      const original = p.document.querySelector("#x");
      if (change === "hide") original.hidden = true;
      else original.replaceWith(original.cloneNode(true));
    }
  });
  try { code(await t.wait(25), "wait_timeout"); }
  finally { t.p.window.close(); }
});

for (const [elapsed, expected] of [[24, null], [25, "wait_timeout"], [26, "wait_timeout"]]) {
  test(`polling qualifies only observations strictly before deadline: observed at ${elapsed}`, async () => {
    let advanceClock;
    const t = timedPage("", ({ advance }) => { advanceClock = advance; });
    t.p.window.setTimeout = (callback) => {
      advanceClock(elapsed);
      t.p.document.body.innerHTML = '<button id="x">Polled</button>';
      queueMicrotask(callback);
      return 1;
    };
    try {
      const result = await t.wait(25);
      if (expected) code(result, expected);
      else assert.equal(result.result.status, "matched");
    } finally { t.p.window.close(); }
  });
}

for (const [change, expected] of [["permission", "permission_denied"], ["tab", "page_changed"],
  ["url", "page_changed"], ["expiry", "request_expired"]]) {
  test(`a timed-out wait still reports ${expected} from final safety confirmation`, async () => {
    const t = timedPage('<button id="x" hidden>Later</button>', ({ p, call, advance }) => {
      if (call === 2) {
        if (change === "permission") return { ok: false };
        if (change === "tab") return { ok: false, error: { code: "page_changed" } };
        if (change === "url") p.window.history.replaceState(null, "", "/changed");
        if (change === "expiry") advance(9000);
      }
    });
    try {
      code(await t.wait(0), expected);
    } finally { t.p.window.close(); }
  });
}

test("timeout during synchronous target discovery cannot qualify a positive wait", async () => {
  const t = timedPage('<button id="x">Ready</button>', () => {});
  t.p.document.querySelector("#x").getClientRects = () => {
    // First observation happens before initial authorize, so move the local
    // clock directly for this read; this is not an async appearance.
    t.advance(25);
    return [{ width: 100, height: 20 }];
  };
  try { code(await t.wait(25), "wait_timeout"); }
  finally { t.p.window.close(); }
});

test("a timed-out ref wait still reports staleness during final confirmation", async () => {
  const p = page('<button id="x" hidden>Later</button>');
  const ref = (await p.call("inspect", { target: { id: "x" } })).result.elements[0].ref;
  let calls = 0;
  p.window.chrome.runtime.sendMessage = async () => {
    if (++calls === 2) p.document.querySelector("#x").remove();
    return { ok: true };
  };
  try {
    code(await wait(p, "visible", { target: { ref }, timeout_ms: 0 }), "stale_target");
    assert.equal(calls, 2);
  } finally { p.window.close(); }
});

test("a deferred authorization does not let a later appearance satisfy an immediate wait", async () => {
  let release, entered;
  const gate = new Promise((resolve) => { release = resolve; });
  const reached = new Promise((resolve) => { entered = resolve; });
  const t = timedPage("", async ({ call }) => { if (call === 1) { entered(); await gate; } });
  try {
    const pending = t.wait(0);
    await reached;
    t.advance(180);
    t.p.document.body.innerHTML = '<button id="x">Deferred appearance</button>';
    release();
    code(await pending, "wait_timeout");
    assert.equal(t.calls(), 2);
  } finally { release(); t.p.window.close(); }
});

for (const [html, ready] of [
  ['<button id="x">Ready</button>', true], ['<button id="x" hidden>Hidden</button>', false],
  ['<button id="x" disabled>Disabled</button>', false], ['<div inert><button id="x">Inert</button></div>', false],
  ['<div aria-disabled="true"><button id="x">ARIA disabled</button></div>', false],
  ['<div class="disable"><button id="x">Disabled class</button></div>', false],
]) test(`actionable combines visibility and shared disabled checks: ${html}`, async () => {
  const p = page(html);
  try {
    const result = await wait(p, "actionable", { timeout_ms: 0 });
    if (ready) assert.equal(result.result.status, "matched");
    else code(result, "wait_timeout");
    if (html.includes("hidden")) assert.equal((await wait(p, "enabled", { timeout_ms: 0 })).result.status, "matched");
  } finally { p.window.close(); }
});

test("actionable never reads field values, acts, or bypasses password/read-only operation protections", async () => {
  const p = page('<input id="password" type="password"><input id="readonly" readonly>');
  for (const input of p.document.querySelectorAll("input")) {
    Object.defineProperty(input, "value", { get: () => assert.fail("wait must not read input values") });
    input.focus = input.click = input.scrollIntoView = () => assert.fail("wait must not act");
  }
  try {
    for (const [id, expected] of [["password", "protected_input"], ["readonly", "target_readonly"]]) {
      const target = { id };
      assert.equal((await wait(p, "actionable", { target, timeout_ms: 0 })).result.status, "matched");
      code(await p.call("fill", { target, value: "not written" }), expected);
      code(await p.call("press_key", { target, key: "Enter" }), expected);
    }
  } finally { p.window.close(); }
});

test("wait_for keeps malformed CSS, navigation, expiry and missing state errors distinct", async () => {
  const p = page("");
  try {
    code(await wait(p, "detached", { target: { css: "[" }, timeout_ms: 0 }), "invalid_selector");
    code(await wait(p, "visible", { timeout_ms: 5 }), "wait_timeout");
    code(await wait(p, "visible", {}, { expires_at: Date.now() - 1 }), "request_expired");
    code(await wait(p, "visible", { timeout_ms: 200 }, { expires_at: Date.now() + 110 }), "request_expired");
    const pending = wait(p, "visible");
    p.window.setTimeout(() => p.window.history.replaceState(null, "", "/changed"), 10);
    code(await pending, "page_changed");
  } finally { p.window.close(); }
});

test("authorization is rechecked on timeout and periodically, even if no target appears", async () => {
  let allowed = true;
  const p = page("", { authorize: async () => ({ ok: allowed }) });
  try {
    const pending = wait(p, "visible", { timeout_ms: 1000 });
    p.window.setTimeout(() => { allowed = false; }, 10);
    code(await pending, "permission_denied");
  } finally { p.window.close(); }
});

test("a match is recomputed after final async authorization, rather than returning a previous state", async () => {
  let calls = 0, p;
  p = page('<button id="x">Ready</button>', { authorize: async () => {
    if (++calls === 2) p.document.querySelector("#x").hidden = true;
    return { ok: true };
  } });
  try { code(await wait(p, "visible", { timeout_ms: 10 }), "wait_timeout"); }
  finally { p.window.close(); }
});

test("wait_for retains full TabSelector checks and page authorization while waiting", async () => {
  const p = browserPage("");
  try {
    const pending = p.f.call("browser.page.wait_for", { tab: { tab_id: 1, title: "Test" }, target: { id: "x" }, timeout_ms: 200 });
    p.window.setTimeout(() => {
      p.f.tabs[0].title = "Changed";
      p.document.body.innerHTML = '<button id="x">Ready</button>';
    }, 10);
    code(await pending, "page_changed");
    p.f.tabs[0].title = "Test";
    const waiting = p.f.call("browser.page.wait_for", { tab: { tab_id: 1 }, target: { id: "missing" }, timeout_ms: 200 });
    p.window.setTimeout(() => { p.f.settings.pageOrigins = []; }, 10);
    code(await waiting, "permission_denied");
  } finally { p.window.close(); }
});
