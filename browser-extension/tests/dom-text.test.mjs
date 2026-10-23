import assert from "node:assert/strict";
import test from "node:test";
import { page } from "./dom-helper.mjs";

test("review: semantic inspect never reads protected input content, including through parents or label references", async () => {
  const p = page('<div id="parent">Public<textarea id="draft">PRIVATE-DRAFT-SENTINEL</textarea>'
    + '<input id="input" value="PRIVATE-INPUT-SENTINEL"><script>PRIVATE-SCRIPT-SENTINEL</script>'
    + '<style>/* PRIVATE-STYLE-SENTINEL */</style><div contenteditable id="editor">PRIVATE-EDIT-SENTINEL</div></div>'
    + '<input id="raw-button" type="button" value="PRIVATE-BUTTON-SENTINEL">'
    + '<button id="referenced" aria-labelledby="draft">Safe</button>');
  for (const input of p.document.querySelectorAll("input,textarea")) {
    Object.defineProperty(input, "value", { get() { throw Error("Do not read current input value"); } });
  }
  Object.defineProperty(p.document, "cookie", { get() { throw Error("Do not read cookies"); } });
  try {
    const response = await p.call("inspect", { limit: 20 });
    assert.equal(response.ok, true, JSON.stringify(response));
    assert.equal(JSON.stringify(response).includes("PRIVATE-"), false);
    const byId = Object.fromEntries(response.result.elements.map((element) => [element.id, element]));
    assert.equal(byId.draft.name, "");
    assert.equal(byId.input.name, "");
    assert.equal(byId.editor.name, "");
    assert.equal(byId["raw-button"].name, "");
    assert.equal(byId.parent.name, "Public");
    assert.equal(byId.referenced.name, "Safe");
    assert.deepEqual(Object.keys(byId.draft).sort(), ["role", "name", "id", "href", "aria_label", "placeholder", "visible",
      "disabled", "read_only", "input_type", "ref", "truncated"].sort());
  } finally { p.window.close(); }
});

test("semantic names and label conditions still work while excluding protected label descendants", async () => {
  const p = page('<button id="plain">Run<span hidden>PRIVATE-HIDDEN-SENTINEL</span><script>PRIVATE-SCRIPT-SENTINEL</script></button>'
    + '<label for="draft">Draft<textarea id="draft">PRIVATE-DRAFT-SENTINEL</textarea></label>'
    + '<textarea id="aria" aria-label="Notes">PRIVATE-ARIA-SENTINEL</textarea>'
    + '<span id="caption">Send<input value="PRIVATE-LABEL-SENTINEL"></span><button id="labelled" aria-labelledby="caption">Icon</button>'
    + '<input id="placeholder" placeholder="Find"><input id="button-input" type="button" aria-label="Confirm">');
  for (const input of p.document.querySelectorAll("input,textarea")) {
    Object.defineProperty(input, "value", { get() { throw Error("Do not read any input value"); } });
  }
  try {
    for (const [id, name] of [["plain", "Run"], ["draft", "Draft"], ["aria", "Notes"], ["labelled", "Send"],
      ["placeholder", "Find"], ["button-input", "Confirm"]]) {
      const response = await p.call("inspect", { target: { id, name } });
      assert.equal(response.ok, true, `${id}: ${JSON.stringify(response)}`);
      assert.equal(response.result.elements[0].name, name);
      assert.equal(JSON.stringify(response).includes("PRIVATE-"), false);
    }
    assert.equal((await p.call("inspect", { target: { label: "Draft", role: "textbox" } })).result.total, 1);
    assert.equal((await p.call("click", { target: { role: "button", name: "Run" } })).result.status, "clicked");
  } finally { p.window.close(); }
});

for (const child of ['<span hidden>hidden-marker</span>', '<span style="display:none">hidden-marker</span>',
  '<span aria-hidden="true">hidden-marker</span>', '<script>private-script</script><style>/* private-style */</style>',
  '<textarea>private-draft</textarea><div contenteditable>private-edit</div>']) {
  test(`review: DOM observed text remains exact for CSS and broad text matching: ${child.slice(0, 35)}`, async () => {
    const p = page(`<div class="send">${child} Send</div>`);
    let clicked;
    p.document.addEventListener("click", (event) => { clicked = event.target; });
    try {
      const probe = await p.call("dom.inspect", { css: ".send" });
      assert.equal(probe.result.elements[0].text, "Send");
      for (const target of [{ css: ".send", text: probe.result.elements[0].text }, { text: "Send" }]) {
        const response = await p.call("click", { target });
        assert.equal(response.ok, true, JSON.stringify(response));
        assert.equal(clicked, p.document.querySelector(".send"));
      }
      assert.equal((await p.call("dom.inspect", { css: ".send", text: "Send" })).result.total, 1);
      assert.equal((await p.call("dom.inspect", { text: "Send" })).result.total, 1);
      for (const target of [{ css: ".send", text: "Sen" }, { css: ".send", text: "Send more" },
        { css: ".send", text: "Send", id: "wrong" }, { css: ".send", text: "Send", role: "button" }]) {
        assert.equal((await p.call("click", { target })).error.code, "target_not_found");
      }
    } finally { p.window.close(); }
  });
}

test("filtered text still deduplicates button children and preserves visible ambiguity and hidden status", async () => {
  const p = page('<button><span>Send</span><script>private</script></button><div class="other" hidden>Send</div>');
  let clicked;
  p.document.addEventListener("click", (event) => { clicked = event.target; });
  try {
    assert.equal((await p.call("click", { target: { text: "Send" } })).result.status, "clicked");
    assert.equal(clicked, p.document.querySelector("button"));
    p.document.querySelector(".other").hidden = false;
    assert.equal((await p.call("click", { target: { text: "Send" } })).error.code, "ambiguous_target");
    p.document.querySelector("button").hidden = true;
    p.document.querySelector(".other").hidden = true;
    assert.equal((await p.call("click", { target: { css: ".other", text: "Send" } })).error.code, "target_hidden");
  } finally { p.window.close(); }
});

for (const phase of ["scroll", "focus"]) test(`text revalidation after ${phase} uses the same filtered, exact rules`, async () => {
  const p = page('<div class="send" tabindex="0"><span hidden>hidden</span>Send</div>');
  const original = p.document.querySelector(".send");
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  const change = () => { original.lastChild.data = "Changed"; };
  original.scrollIntoView = () => { if (phase === "scroll") change(); };
  original.addEventListener("focus", () => { if (phase === "focus") change(); });
  try {
    assert.equal((await p.call("click", { target: { css: ".send", text: "Send" } })).error.code, "stale_target");
    assert.equal(clicks, 0);
  } finally { p.window.close(); }
});

test("snapshot-clipped text cannot be used as a prefix match for a long control", async () => {
  const value = "Long text ".repeat(40).trim();
  const p = page(`<div class="send">${value}</div>`);
  try {
    const probe = await p.call("dom.inspect", { css: ".send" });
    const node = probe.result.elements[0];
    assert.equal(node.text.length, 160);
    assert.equal(node.truncated, true);
    assert.equal(probe.result.truncated, true);
    assert.equal((await p.call("click", { target: { css: ".send", text: node.text } })).error.code, "target_not_found");
  } finally { p.window.close(); }
});

test("incomplete candidate reads cannot silently establish a unique text match", async () => {
  const p = page('<div class="send">Send</div><div class="send">Send' + ' '.repeat(3000) + '</div>');
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  try {
    for (const target of [{ css: ".send", text: "Send" }, { text: "Send" }]) {
      const response = await p.call("click", { target });
      assert.equal(response.ok, false);
      assert.equal(response.error.code, "page_too_large");
    }
    assert.equal(clicks, 0);
  } finally { p.window.close(); }
});

test("global text scanning exhaustion never clicks a previously seen unique candidate", async () => {
  const p = page('<span>Send</span>' + '<i>Other</i>'.repeat(10001));
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  try {
    assert.equal((await p.call("click", { target: { text: "Send" } })).error.code, "page_too_large");
    assert.equal(clicks, 0);
  } finally { p.window.close(); }
});

test("review: complete DOM results and projected parents are not truncated", async () => {
  const p = page('<div class="send">Send</div>');
  try {
    const response = await p.call("dom.inspect", { css: ".send" });
    assert.equal(response.result.truncated, false);
    const [node] = response.result.elements;
    assert.equal(node.truncated, false);
    assert.ok(node.parents.every((parent) => parent.truncated === false));
  } finally { p.window.close(); }
});

test("depth zero and privacy omissions are intentional projections, not resource truncation", async () => {
  const p = page('<section><div>Send</div><textarea>Private draft</textarea><div contenteditable><b>Private edit</b></div></section>');
  try {
    for (const css of ["section", "textarea", "[contenteditable]"]) {
      const response = await p.call("dom.inspect", { css, depth: 0 });
      assert.equal(response.result.truncated, false, css);
      assert.equal(response.result.elements[0].truncated, false, css);
      assert.equal(response.result.elements[0].children.length, 0);
    }
  } finally { p.window.close(); }
});

test("actual child and result limits mark truncation at the affected node and result", async () => {
  const p = page('<section>' + '<div><i>Item</i></div>'.repeat(6) + '</section>');
  try {
    const childLimited = await p.call("dom.inspect", { css: "section", depth: 1 });
    assert.equal(childLimited.result.elements[0].children.length, 5);
    assert.equal(childLimited.result.elements[0].truncated, true);
    assert.equal(childLimited.result.truncated, true);
    assert.equal(childLimited.result.elements[0].children[0].truncated, false);
    const resultLimited = await p.call("dom.inspect", { css: "section > div", limit: 2, depth: 0 });
    assert.equal(resultLimited.result.total, 6);
    assert.equal(resultLimited.result.elements.length, 2);
    assert.equal(resultLimited.result.truncated, true);
    assert.ok(resultLimited.result.elements.every((node) => node.truncated === false));
  } finally { p.window.close(); }
});

test("privacy redaction alone is not resource truncation", async () => {
  const p = page('<div class="' + 'A'.repeat(300) + '" aria-label="Bearer private-auth">token=private-token</div>');
  try {
    const response = await p.call("dom.inspect", { css: "div", depth: 0 });
    assert.equal(response.result.elements[0].class, "[redacted]");
    assert.equal(response.result.elements[0].attributes["aria-label"], "[redacted]");
    assert.equal(response.result.elements[0].truncated, false);
    assert.equal(response.result.truncated, false);
  } finally { p.window.close(); }
});

test("descendant resource truncation propagates to its containing node", async () => {
  const p = page('<section><div>' + '<i>Item</i>'.repeat(6) + '</div></section>');
  try {
    const response = await p.call("dom.inspect", { css: "section", depth: 2 });
    assert.equal(response.result.elements[0].children[0].truncated, true);
    assert.equal(response.result.elements[0].truncated, true);
    assert.equal(response.result.truncated, true);
  } finally { p.window.close(); }
});

test("byte budget exhaustion is visible even before the node count limit", async () => {
  const p = page('<section>' + '<div>Send</div>'.repeat(10) + '</section>');
  const value = "中文".repeat(40);
  for (const element of p.document.querySelectorAll("html,body,section,div")) {
    for (const attribute of ["id", "class", "type", "name", "aria-label"]) element.setAttribute(attribute, value);
    element.setAttribute("role", value.slice(0, 64));
  }
  try {
    const response = await p.call("dom.inspect", { css: "section > div", depth: 0, limit: 10 });
    assert.equal(response.ok, true, JSON.stringify(response));
    assert.equal(response.result.total, 10);
    assert.ok(response.result.elements.length < 10);
    assert.equal(response.result.truncated, true);
    assert.ok(new TextEncoder().encode(JSON.stringify(response)).length < 40 * 1024);
    assert.ok(response.result.elements.length * 3 < 40);
  } finally { p.window.close(); }
});
