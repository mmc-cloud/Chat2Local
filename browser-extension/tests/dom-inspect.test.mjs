import assert from "node:assert/strict";
import test from "node:test";
import { page } from "./dom-helper.mjs";

test("local CSS DOM probe returns filtered structure and a reusable selector; observe after a custom click", async () => {
  const p = page('<main><section id="compose"><div class="actions"><div class="send-message" aria-label="Send" data-key="private">'
    + '<span>Send</span></div></div><output>Waiting</output></section></main>');
  let clicks = 0;
  const send = p.document.querySelector(".send-message");
  send.addEventListener("click", () => { clicks++; p.document.querySelector("output").textContent = "Done"; });
  try {
    const result = await p.call("dom.inspect", { css: ".actions > div", scope: "#compose", depth: 1 });
    assert.equal(result.ok, true, JSON.stringify(result));
    const [node] = result.result.elements;
    assert.equal(node.tag, "div");
    assert.equal(node.class, "send-message");
    assert.equal(node.text, "Send");
    assert.equal(node.attributes["aria-label"], "Send");
    assert.equal(node.attributes["data-key"], undefined);
    assert.equal(node.children[0].tag, "span");
    assert.equal(node.parents.length, 2);
    assert.equal(node.parents[1].id, "compose");
    assert.equal(p.document.querySelectorAll(node.css).length, 1);
    assert.equal(p.document.querySelector(node.css), send);
    assert.equal(clicks, 0);
    assert.equal(p.document.activeElement, p.document.body);
    const click = await p.call("click", { target: { css: node.css, text: "Send" } });
    assert.equal(click.result.status, "clicked");
    assert.equal(click.result.event_is_trusted, false);
    assert.equal(clicks, 1);
    assert.equal((await p.call("dom.inspect", { css: "output" })).result.elements[0].text, "Done");
    assert.equal((await p.call("click", { target: { css: node.css, text: "Other" } })).error.code, "target_not_found");
  } finally { p.window.close(); }
});

test("text discovery finds custom spans without matching incidental ancestor containers", async () => {
  const p = page('<main id="parent"><div><span class="custom">Send</span></div></main>');
  const clicked = [];
  p.document.addEventListener("click", (event) => clicked.push(event.target));
  try {
    const found = await p.call("dom.inspect", { text: "Send", tag: "span" });
    assert.equal(found.result.total, 1);
    assert.equal(found.result.elements[0].tag, "span");
    assert.equal((await p.call("click", { target: { text: "Send" } })).result.status, "clicked");
    assert.equal(clicked[0], p.document.querySelector("span"));
    assert.equal((await p.call("inspect", { target: { css: "span.custom", text: "Send" } })).result.total, 1);
    assert.equal((await p.call("dom.inspect", { text: "Send", tag: "div" })).result.elements[0].tag, "div");
  } finally { p.window.close(); }
});

test("layout roles and aria labels do not turn incidental text ancestors into click targets", async () => {
  const p = page('<div role="presentation" aria-label="Composer"><div role="group"><span>Send</span></div></div>');
  let target;
  p.document.addEventListener("click", (event) => { target = event.target; });
  try {
    assert.equal((await p.call("click", { target: { text: "Send" } })).result.status, "clicked");
    assert.equal(target, p.document.querySelector("span"));
  } finally { p.window.close(); }
});

test("legacy name-only lookup cannot click an incidental id-bearing parent after custom click support", async () => {
  const p = page('<div id="wrapper"><span class="custom">Send</span></div>');
  let target;
  p.document.addEventListener("click", (event) => { target = event.target; });
  try {
    assert.equal((await p.call("click", { target: { name: "Send" } })).result.status, "clicked");
    assert.equal(target, p.document.querySelector("span"));
    assert.equal((await p.call("click", { target: { id: "wrapper", name: "Send" } })).result.status, "clicked");
    assert.equal(target, p.document.querySelector("#wrapper"));
  } finally { p.window.close(); }
});

test("read-only DOM probing never touches input value getters or cookie APIs and does not cross frames or shadow roots", async () => {
  const p = page('<main><input><textarea>Private</textarea><iframe srcdoc="<div>Frame</div>"></iframe><div id="host"></div></main>');
  for (const element of p.document.querySelectorAll("input,textarea")) {
    Object.defineProperty(element, "value", { get() { throw Error("Input value must not be read"); } });
  }
  Object.defineProperty(p.document, "cookie", { get() { throw Error("Cookie must not be read"); } });
  p.document.querySelector("#host").attachShadow({ mode: "open" }).innerHTML = '<div class="shadow">Private shadow</div>';
  try {
    assert.equal((await p.call("dom.inspect", { css: "main", depth: 3 })).ok, true);
    assert.equal((await p.call("dom.inspect", { css: ".shadow" })).error.code, "target_not_found");
    assert.equal((await p.call("dom.inspect", { text: "Frame" })).error.code, "target_not_found");
  } finally { p.window.close(); }
});

test("name fallback preserves native input candidates when a plain container also matches", async () => {
  const p = page('<input aria-label="Send"><div id="wrapper"><span>Send</span></div>');
  try {
    assert.equal((await p.call("click", { target: { name: "Send" } })).error.code, "ambiguous_target");
    p.document.querySelector("input").hidden = true;
    assert.equal((await p.call("click", { target: { name: "Send" } })).result.status, "clicked");
  } finally { p.window.close(); }
});

test("DOM summaries redact common credentials and retain strict read-only behavior", async () => {
  const jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signature";
  const opaque = "A".repeat(40);
  const p = page(`<div id="probe" aria-label="Bearer private-auth" class="${opaque}">token=private-token ${jwt}</div>`);
  const before = p.document.documentElement.outerHTML;
  try {
    const result = await p.call("dom.inspect", { css: "#probe" });
    const output = JSON.stringify(result);
    for (const secret of [jwt, opaque, "private-auth", "private-token"]) assert.equal(output.includes(secret), false, secret);
    assert.equal(p.document.documentElement.outerHTML, before);
    assert.equal(p.document.activeElement, p.document.body);
  } finally { p.window.close(); }
});

test("DOM snapshot verifies URL and expiry again after the last read", async () => {
  for (const change of ["url", "expiry"]) {
    const p = page("<div>Read</div>");
    const expiresAt = Date.now() + 10000;
    p.document.documentElement.getClientRects = () => {
      if (change === "url") p.window.history.replaceState(null, "", "/changed");
      else p.window.Date.now = () => expiresAt;
      return [{ width: 10, height: 10 }];
    };
    try {
      const result = await p.call("dom.inspect", { css: "html", depth: 0 }, { expires_at: expiresAt });
      assert.equal(result.error.code, change === "url" ? "page_changed" : "request_expired");
    } finally { p.window.close(); }
  }
});

test("text targeting keeps standard button semantics over its inner span and never overlooks an unrelated duplicate", async () => {
  const p = page('<div id="wrapper"><button><span>Send</span></button></div>');
  let target;
  p.document.addEventListener("click", (event) => { target = event.target; });
  try {
    assert.equal((await p.call("click", { target: { text: "Send" } })).result.status, "clicked");
    assert.equal(target, p.document.querySelector("button"));
    p.document.body.insertAdjacentHTML("beforeend", "<span>Send</span>");
    assert.equal((await p.call("click", { target: { text: "Send" } })).error.code, "ambiguous_target");
  } finally { p.window.close(); }
});

for (const css of ["[", "div >> span", "div,"]) test(`invalid selector safely rejected: ${css}`, async () => {
  const p = page('<div class="custom">Send</div>');
  try {
    for (const [operation, args] of [["dom.inspect", { css }], ["dom.inspect", { css: "div", scope: css }],
      ["click", { target: { css } }], ["inspect", { target: { css } }]]) {
      assert.equal((await p.call(operation, args)).error.code, "invalid_selector");
    }
  } finally { p.window.close(); }
});

test("DOM scope must be unique, stays local and is rechecked after authorization", async () => {
  let p;
  p = page('<section class="scope"><span>Send</span></section><section class="scope"><span>Other</span></section>');
  try {
    assert.equal((await p.call("dom.inspect", { css: "span", scope: ".missing" })).error.code, "scope_not_found");
    assert.equal((await p.call("dom.inspect", { css: "span", scope: ".scope" })).error.code, "ambiguous_scope");
    assert.equal((await p.call("dom.inspect", { css: "span", scope: ".scope:last-child", text: "Send" })).error.code, "target_not_found");
  } finally { p.window.close(); }
  const q = page('<section id="scope"><span>Send</span></section>', { authorize: async () => {
    q.document.querySelector("section").remove(); return { ok: true };
  } });
  try { assert.equal((await q.call("dom.inspect", { css: "span", scope: "#scope" })).error.code, "scope_not_found"); }
  finally { q.window.close(); }
});

test("DOM probe protects input contents, script/hidden secrets, URLs and non-whitelisted attributes at every depth", async () => {
  const secrets = ["PASSWORD-SENTINEL", "TEXTAREA-SENTINEL", "CURRENT-INPUT-SENTINEL", "COOKIE-SENTINEL", "TOKEN-SENTINEL",
    "SCRIPT-SENTINEL", "HIDDEN-SENTINEL", "EDITABLE-SENTINEL", "LINK-SENTINEL", "SELECT-SENTINEL"];
  const p = page('<section id="probe" onclick="PASSWORD-SENTINEL" data-token="TOKEN-SENTINEL">Safe'
    + '<input type="password" value="PASSWORD-SENTINEL"><textarea>TEXTAREA-SENTINEL</textarea><input value="COOKIE-SENTINEL">'
    + '<div id="csrf-token">TOKEN-SENTINEL</div><script>SCRIPT-SENTINEL</script><div hidden>HIDDEN-SENTINEL</div>'
    + '<div contenteditable><span>EDITABLE-SENTINEL</span></div><a href="/?token=LINK-SENTINEL">Link</a>'
    + '<select><option>SELECT-SENTINEL</option></select></section>');
  p.document.querySelectorAll("input")[1].value = "CURRENT-INPUT-SENTINEL";
  try {
    const results = [];
    for (const css of ["#probe", "input", "textarea", "[contenteditable] span", "#csrf-token", "script", "[hidden]", "option"]) {
      const result = await p.call("dom.inspect", { css, depth: 3, limit: 10 });
      assert.equal(result.ok, true, JSON.stringify(result));
      results.push(result);
    }
    const serialized = JSON.stringify(results);
    for (const secret of secrets) assert.equal(serialized.includes(secret), false, secret);
    for (const key of ["onclick", "data-token", '"value":', '"href":']) assert.equal(serialized.includes(key), false, key);
    const input = results[1].result.elements[0];
    assert.equal(input.text, "");
    assert.equal(Object.keys(input.attributes).length, 0);
  } finally { p.window.close(); }
});

test("DOM probe caps results, depth, children, attributes, text and UTF-8 size", async () => {
  const html = '<section>' + ('<div id="x" class="c" role="button" aria-label="label" aria-disabled="false" aria-hidden="false" aria-expanded="false">'
    + '<span>' + "中文".repeat(300) + "</span>" + '<i>Child</i>'.repeat(8) + '</div>').repeat(20) + '</section>';
  const p = page(html);
  try {
    const result = await p.call("dom.inspect", { css: "section > div", limit: 10, depth: 3 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.result.total, 20);
    assert.ok(result.result.elements.length <= 10);
    assert.equal(result.result.truncated, true);
    let nodes = 0;
    const visit = (node, depth) => {
      nodes++;
      assert.ok(node.text.length <= 160);
      assert.ok(Object.keys(node.attributes).length <= 20); // Fixed interaction metadata whitelist, including ARIA relations.
      assert.ok(node.children.length <= 5);
      assert.ok(depth <= 3);
      node.children.forEach((child) => visit(child, depth + 1));
    };
    result.result.elements.forEach((node) => { visit(node, 0); node.parents.forEach((parent) => visit(parent, 0)); });
    assert.ok(nodes <= 40);
    assert.ok(new TextEncoder().encode(JSON.stringify(result)).length < 40 * 1024);
    const flat = await p.call("dom.inspect", { css: "section", depth: 0 });
    assert.equal(flat.result.elements[0].children.length, 0);
  } finally { p.window.close(); }
});

test("broad or excessive DOM discovery fails safely while explicit local CSS stays usable", async () => {
  const p = page('<section id="local"><div class="send">Send</div></section>' + '<i></i>'.repeat(10001));
  try {
    assert.equal((await p.call("dom.inspect", { tag: "i" })).error.code, "page_too_large");
    assert.equal((await p.call("dom.inspect", { text: "Send" })).error.code, "page_too_large");
    assert.equal((await p.call("dom.inspect", { css: ".send", scope: "#local" })).result.total, 1);
  } finally { p.window.close(); }
});

for (const [attributes, expected] of [["hidden", "target_hidden"], ['aria-disabled="true"', "target_disabled"],
  ["inert", "target_disabled"], ['class="disabled"', "target_disabled"], ['class="is-disabled"', "target_disabled"]]) {
  test(`custom clicks respect ${attributes}`, async () => {
    const p = page(`<div ${attributes}>Send</div>`);
    let clicks = 0;
    p.document.addEventListener("click", () => clicks++);
    try {
      assert.equal((await p.call("click", { target: { css: "div", text: "Send" } })).error.code, expected);
      assert.equal(clicks, 0);
    } finally { p.window.close(); }
  });
}

test("unknown CSS classes do not imply a disabled control; visible duplicates retain ambiguity", async () => {
  const p = page('<div class="disabled-looking-label">Send</div><div hidden>Send</div>');
  try {
    assert.equal((await p.call("click", { target: { css: "div", text: "Send" } })).result.status, "clicked");
    p.document.querySelector("[hidden]").hidden = false;
    assert.equal((await p.call("click", { target: { css: "div", text: "Send" } })).error.code, "ambiguous_target");
    for (const element of p.document.querySelectorAll("div")) element.hidden = true;
    assert.equal((await p.call("click", { target: { css: "div", text: "Send" } })).error.code, "target_hidden");
  } finally { p.window.close(); }
});

for (const phase of ["scroll", "focus"]) for (const change of ["replace", "remove", "css", "duplicate"]) {
  test(`custom target identity and CSS remain valid after ${phase}: ${change}`, async () => {
    const p = page('<div tabindex="0" class="send">Send</div>');
    const original = p.document.querySelector("div");
    let clicks = 0;
    p.document.addEventListener("click", () => clicks++);
    const mutate = () => {
      if (change === "replace") original.replaceWith(original.cloneNode(true));
      if (change === "remove") original.remove();
      if (change === "css") original.className = "other";
      if (change === "duplicate") p.document.body.append(original.cloneNode(true));
    };
    original.scrollIntoView = () => { if (phase === "scroll") mutate(); };
    original.addEventListener("focus", () => { if (phase === "focus") mutate(); });
    try {
      const response = await p.call("click", { target: { css: ".send", text: "Send" } });
      assert.equal(response.error.code, change === "duplicate" ? "ambiguous_target" : "stale_target");
      assert.equal(clicks, 0);
    } finally { p.window.close(); }
  });
}

test("DOM probe retains authorization, URL, expiry and sender checks and never acts", async () => {
  const p = page('<div>Send</div>', { authorize: async () => ({ ok: false, error: { code: "permission_denied" } }) });
  try {
    assert.equal((await p.call("dom.inspect", { css: "div" })).error.code, "permission_denied");
    assert.equal((await p.call("dom.inspect", { css: "div" }, { expected_url: "https://other.example/" })).error.code, "page_changed");
    assert.equal((await p.call("dom.inspect", { css: "div" }, { expires_at: Date.now() - 1 })).error.code, "request_expired");
    assert.equal((await p.dispatch({ type: "chat2local.dom", operation: "dom.inspect", arguments: { css: "div" } }, { id: "other" })).ignored, true);
  } finally { p.window.close(); }
});
