import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { browserPage, fixture } from "./operations-helper.mjs";

const discovery = JSON.parse(readFileSync(new URL("../capability-schemas.json", import.meta.url), "utf8"));
const entry = (name) => discovery.capabilities.find((item) => item.name === name);
const tab = { tab_id: 1 };

test("all observation, action and ambiguity summaries redact attributes and URLs before clipping", async () => {
  const p = browserPage('<a href="https://reader:PRIVATE-PASSWORD@example.com/jobs?access_token=PRIVATE-TOKEN#PRIVATE-FRAGMENT">Jobs</a>'
    + '<button aria-label="token=PRIVATE-SECRET">Go</button><input placeholder="Bearer PRIVATE-CREDENTIAL">'
    + '<button id="safe" aria-label="' + 'Public '.repeat(30) + 'token=PRIVATE-LONG-SECRET">Safe</button>'
    + '<button id="token=PRIVATE-ID" aria-label="Same">Same</button><button>Same</button>');
  p.document.addEventListener("click", (event) => event.preventDefault());
  try {
    for (const operation of ["browser.page.inspect", "browser.page.dom.inspect", "browser.page.read", "browser.page.click", "browser.page.fill"]) {
      const args = operation.endsWith("dom.inspect") ? { css: "body", depth: 3 } : operation.endsWith("click")
        ? { target: { css: "a" } } : operation.endsWith("fill") ? { target: { css: "input" }, value: "PRIVATE-INPUT-VALUE" } : {};
      const response = await p.f.call(operation, { tab, ...args });
      assert.equal(response.ok, true, JSON.stringify(response));
      assert.equal(JSON.stringify(response).includes("PRIVATE-"), false, operation);
    }
    const ambiguous = await p.f.call("browser.page.click", { tab, target: { name: "Same" } });
    assert.equal(ambiguous.error.code, "ambiguous_target");
    assert.equal(JSON.stringify(ambiguous).includes("PRIVATE-"), false);
    assert.ok(ambiguous.error.details.candidates.every((item) => item.ref.startsWith("el_")));
    const summaries = (await p.f.call("browser.page.inspect", { tab })).result.elements;
    assert.equal(summaries.find((item) => item.role === "link").href, "https://example.com/jobs");
    assert.ok(summaries.every((item) => "name" in item && "href" in item));
    assert.equal(summaries.find((item) => item.id === "safe").truncated, true);
  } finally { p.window.close(); }
});

test("redaction never exposes a credential prefix at clipping or oversized-attribute boundaries", async () => {
  const p = browserPage('<button>Safe</button><input>');
  const button = p.document.querySelector("button");
  try {
    for (const raw of ['token="PRIVATE-ONE PRIVATE-TWO"', 'authorization=bearer PRIVATE-SHORT',
      "token=" + "PRIVATE-".repeat(1000), "Bearer " + "PRIVATE-".repeat(1000),
      "prefix ".repeat(100) + "password=" + "PRIVATE-".repeat(1000), "PRIVATE-".repeat(10000)]) {
      button.setAttribute("aria-label", raw);
      button.id = raw;
      p.document.querySelector("input").setAttribute("placeholder", raw);
      for (const [capability, args] of [["browser.page.inspect", {}], ["browser.page.dom.inspect", { css: "body", depth: 1 }], ["browser.page.read", {}]]) {
        const response = await p.f.call(capability, { tab, ...args });
        assert.equal(response.ok, true, JSON.stringify(response));
        assert.equal(JSON.stringify(response).includes("PRIVATE-"), false, capability);
      }
    }
  } finally { p.window.close(); }
});

test("tab metadata and ambiguous-tab candidates use the same safe display rules", async () => {
  const tabs = [1, 2].map((id) => ({ id, windowId: 7, title: "token=PRIVATE-TITLE", url: `https://test.example/jobs?token=PRIVATE-${id}#PRIVATE-HASH` }));
  const f = fixture({ tabs });
  for (const [capability, args] of [["browser.tabs.list", {}], ["browser.tabs.resolve", { tab: { tab_id: 1 } }],
    ["browser.tabs.resolve", { tab: { title: tabs[0].title } }]]) {
    const result = await f.call(capability, args);
    assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
  }
});

test("read -> exact ref click distinguishes identical link text with private queries", async () => {
  const p = browserPage('<a href="/jobs?filter=python&token=PRIVATE-PYTHON">Read jobs</a>'
    + '<a href="/jobs?filter=java&token=PRIVATE-JAVA">Read jobs</a>');
  const clicked = [];
  p.document.querySelectorAll("a").forEach((node, index) => node.addEventListener("click", (event) => { event.preventDefault(); clicked.push(index); }));
  try {
    const read = await p.read();
    assert.equal(JSON.stringify(read).includes("PRIVATE-"), false);
    const links = read.result.blocks.filter((item) => item.type === "link");
    assert.equal(links[0].url, links[1].url);
    assert.notEqual(links[0].ref, links[1].ref);
    assert.equal((await p.f.call("browser.page.click", { tab, target: { text: "Read jobs" } })).error.code, "ambiguous_target");
    for (const index of [1, 0]) {
      assert.equal((await p.f.call("browser.page.click", { tab, target: { ref: links[index].ref } })).result.status, "clicked");
    }
    assert.deepEqual(clicked, [1, 0]);
    // Known original exact href and explicit CSS retain their previous semantics.
    assert.equal((await p.f.call("browser.page.click", { tab, target: { href: "https://test.example/jobs?filter=python&token=PRIVATE-PYTHON" } })).ok, true);
    assert.equal((await p.f.call("browser.page.click", { tab, target: { css: "a:nth-child(2)" } })).ok, true);
    assert.deepEqual(clicked, [1, 0, 0, 1]);
  } finally { p.window.close(); }
});

for (const change of ["replace", "reorder", "remove", "attribute", "text", "unrelated"]) test(`old refs never retarget after DOM ${change}`, async () => {
  const p = browserPage('<button>Same</button><button>Same</button><p>Other</p>');
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  try {
    const read = await p.read(), ref = read.result.blocks.find((item) => item.type === "control").ref;
    const node = p.document.querySelector("button");
    if (change === "replace") node.replaceWith(node.cloneNode(true));
    if (change === "reorder") p.document.body.append(node);
    if (change === "remove") node.remove();
    if (change === "attribute") node.setAttribute("data-new", "state");
    if (change === "text") node.firstChild.data = "Changed";
    if (change === "unrelated") p.document.querySelector("p").textContent = "Other changed";
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref } })).error.code, "stale_target");
    assert.equal(clicks, 0);
    const current = await p.read();
    const fresh = current.result.blocks.find((item) => item.type === "control").ref;
    assert.notEqual(fresh, ref);
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref: fresh } })).ok, true);
    assert.equal(clicks, 1);
  } finally { p.window.close(); }
});

test("refs remain subject to permission, ANDed conditions, document and target checks", async () => {
  const p = browserPage('<button>Send</button><input aria-label="Query">');
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  try {
    const inspected = await p.f.call("browser.page.inspect", { tab });
    const ref = inspected.result.elements.find((node) => node.role === "button").ref;
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref, name: "Other" } })).error.code, "target_not_found");
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref: "el_unknown" } })).error.code, "stale_target");
    const dom = await p.f.call("browser.page.dom.inspect", { tab, css: "input" });
    assert.equal((await p.f.call("browser.page.fill", { tab, target: { ref: dom.result.elements[0].ref }, value: "Typed" })).ok, true);
    p.f.consent.length = 0;
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref } })).error.code, "permission_denied");
    assert.equal(clicks, 0);
    p.f.consent.push("https://test.example/*");
    p.dom.reconfigure({ url: "https://test.example/other" });
    p.f.tabs[0].url = "https://test.example/other";
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref } })).error.code, "stale_target");
    assert.equal(clicks, 0);
    const fresh = (await p.read()).result.blocks.find((item) => item.type === "control").ref;
    assert.notEqual(fresh, ref);
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref: fresh } })).ok, true);
    assert.equal(clicks, 1);
  } finally { p.window.close(); }
});

for (const phase of ["scroll", "focus"]) test(`ref identity is checked again after ${phase}`, async () => {
  const p = browserPage('<button>Send</button>');
  const node = p.document.querySelector("button");
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  if (phase === "scroll") node.scrollIntoView = () => node.replaceWith(node.cloneNode(true));
  else node.addEventListener("focus", () => p.document.body.prepend(p.document.createElement("div")));
  try {
    const ref = (await p.read()).result.blocks.find((item) => item.type === "control").ref;
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref } })).error.code, "stale_target");
    assert.equal(clicks, 0);
  } finally { p.window.close(); }
});

test("disabled classes change state without inventing control semantics", async () => {
  const p = browserPage('<div class="send-message">发送</div><button>Native</button><div role="button">Semantic</div>');
  try {
    const node = p.document.querySelector(".send-message");
    for (const marker of ["", "disable", "disabled", "is-disabled"]) {
      node.className = `send-message ${marker}`;
      const read = await p.read();
      const custom = read.result.blocks.find((item) => item.text === "发送");
      assert.equal(custom.type, "text");
      assert.equal(custom.disabled, marker !== "");
      assert.equal(read.result.overview.controls, 2);
      if (marker) assert.equal((await p.f.call("browser.page.click", { tab, target: { css: ".send-message" } })).error.code, "target_disabled");
    }
  } finally { p.window.close(); }
});

test("discovery-driven task: unique tab, continuation, semantic vs DOM discovery and disabled stop", async () => {
  assert.match(entry("browser.page.read").result_contract.next_cursor, /continuation/);
  assert.match(entry("browser.page.inspect").description, /semantic/);
  assert.match(entry("browser.page.dom.inspect").description, /custom/);
  assert.match(discovery.error_guidance.target_disabled, /stop/i);
  const p = browserPage('<h1>Guide</h1><p>' + 'Readable text '.repeat(1000) + '</p>'
    + '<button disabled>Send</button><div class="custom-send">Custom action</div>');
  let clicks = 0;
  p.document.addEventListener("click", () => clicks++);
  try {
    const tabs = await p.f.call("browser.tabs.list", {});
    const nativeId = tabs.result.tabs[0].tab_id;
    const resolved = await p.f.call("browser.tabs.resolve", { tab: { tab_id: nativeId } });
    assert.equal(resolved.ok, true);
    let result = (await p.read({ max_chars: 1000 })).result, pages = 1;
    while (result.has_more) {
      result = (await p.read({ cursor: result.next_cursor, max_chars: 1000 })).result;
      assert.ok(++pages < 50);
    }
    assert.ok(pages > 1);
    const send = (await p.f.call("browser.page.inspect", { tab, target: { role: "button", name: "Send" } })).result.elements[0];
    assert.equal(send.disabled, true);
    assert.equal((await p.f.call("browser.page.click", { tab, target: { ref: send.ref } })).error.code, "target_disabled");
    const custom = await p.f.call("browser.page.dom.inspect", { tab, text: "Custom action", tag: "div" });
    assert.ok(custom.result.elements[0].ref);
    assert.equal(clicks, 0);
    const first = (await p.read({ max_chars: 1000 })).result;
    p.document.querySelector("p").textContent = "Changed";
    assert.equal((await p.read({ cursor: first.next_cursor })).error.code, "page_changed");
    assert.equal((await p.read()).ok, true);
    assert.equal((await p.f.call("browser.page.click", { tab, target: {} })).error.code, "invalid_arguments");
    assert.equal((await p.f.call("browser.page.click", { tab, target: { css: ".missing" } })).error.code, "target_not_found");
  } finally { p.window.close(); }
});

test("each discovered success contract covers the actual packaged operation result", async () => {
  const p = browserPage('<h1>Example</h1><button>Run</button><input aria-label="Query"><div>Custom</div>');
  const scenarios = [
    ["browser.tabs.create", { url: "https://test.example/?token=PRIVATE-URL" }],
    ["browser.tabs.list", {}], ["browser.tabs.resolve", { tab }],
    ["browser.page.read", { tab }], ["browser.page.inspect", { tab }],
    ["browser.page.dom.inspect", { tab, text: "Custom", tag: "div" }],
    ["browser.page.click", { tab, target: { role: "button", name: "Run" } }],
    ["browser.page.fill", { tab, target: { role: "textbox", name: "Query" }, value: "Text" }],
  ];
  try {
    for (const [capability, args] of scenarios) {
      const result = await p.f.call(capability, args);
      assert.equal(result.ok, true, JSON.stringify(result));
      assert.deepEqual(Object.keys(result.result).sort(), Object.keys(entry(capability).result_contract).sort(), capability);
      assert.equal(JSON.stringify(result).includes("PRIVATE-"), false);
    }
  } finally { p.window.close(); }
});

test("refs cannot be reused in another document even at the same URL", async () => {
  const p = browserPage('<button>Same</button>'), other = browserPage('<button>Same</button>');
  let clicks = 0;
  other.document.addEventListener("click", () => clicks++);
  try {
    const ref = (await p.read()).result.blocks.find((item) => item.type === "control").ref;
    assert.equal((await other.f.call("browser.page.click", { tab, target: { ref } })).error.code, "stale_target");
    assert.equal(clicks, 0);
  } finally { p.window.close(); other.window.close(); }
});
