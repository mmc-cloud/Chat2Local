import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { JSDOM } from "jsdom";

test("website permissions are requested synchronously from the settings form gesture", async () => {
  const dom = new JSDOM(readFileSync(new URL("../options.html", import.meta.url), "utf8"), {
    url: "https://extension.test/", runScripts: "outside-only",
  });
  const { window } = dom;
  const storage = { pageOrigins: [] }, requests = [];
  let inGesture = false;
  window.chrome = { runtime: { id: "test-extension" },
    storage: { local: { get: async () => storage, set: async (value) => Object.assign(storage, value) },
      session: { get: async () => ({}) }, onChanged: { addListener() {} } },
    permissions: { request: async (value) => { assert.equal(inGesture, true); requests.push(value); return true; },
      remove: async () => true },
  };
  try {
    window.eval(readFileSync(new URL("../browser-operations.js", import.meta.url), "utf8"));
    window.eval(readFileSync(new URL("../options.js", import.meta.url), "utf8"));
    await new Promise((resolve) => setImmediate(resolve));
    window.document.querySelector("#website-url").value = "https://test.example/path";
    inGesture = true;
    window.document.querySelector("#website-permission").dispatchEvent(new window.Event("submit", { cancelable: true }));
    inGesture = false;
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(requests.length, 1);
    assert.equal(requests[0].origins[0], "https://test.example/*");
    assert.ok(storage.pageOrigins.includes("https://test.example/*"));
    window.document.querySelector("#authorized-websites button").click();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(storage.pageOrigins.length, 0);
  } finally { window.close(); }
});
