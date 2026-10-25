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
      remove: async () => true, contains: async () => false },
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

test("all-site grants require a gesture and consent, and revocation disables the internal grant first", async () => {
  const dom = new JSDOM(readFileSync(new URL("../options.html", import.meta.url), "utf8"), {
    url: "https://extension.test/", runScripts: "outside-only",
  });
  const storage = { pageOrigins: ["https://kept.example/*"] };
  let inGesture = false, granted = false, failRemove = false, requests = 0;
  const { window } = dom;
  window.chrome = { runtime: { id: "test-extension" }, storage: {
    local: { get: async () => storage, set: async (value) => Object.assign(storage, value) },
    session: { get: async () => ({}) }, onChanged: { addListener() {} },
  }, permissions: {
    contains: async () => granted,
    request: async ({ origins }) => {
      assert.equal(inGesture, true); requests++;
      assert.deepEqual(Array.from(origins), ["http://*/*", "https://*/*"]);
      return granted;
    },
    remove: async () => { assert.equal(storage.allWebsites, false); if (failRemove) throw Error("denied"); granted = false; return true; },
  } };
  const flush = () => new Promise((resolve) => setImmediate(resolve));
  try {
    window.eval(readFileSync(new URL("../browser-operations.js", import.meta.url), "utf8"));
    window.eval(readFileSync(new URL("../options.js", import.meta.url), "utf8"));
    await flush(); assert.equal(requests, 0); // Loading options does not grant anything.
    inGesture = true; window.document.querySelector("#allow-all-websites").click(); inGesture = false;
    await flush(); assert.notEqual(storage.allWebsites, true);
    granted = true;
    inGesture = true; window.document.querySelector("#allow-all-websites").click(); inGesture = false;
    await flush(); assert.equal(storage.allWebsites, true);
    assert.ok(window.document.querySelector("#all-websites-status").textContent.includes("enabled"));
    failRemove = true;
    window.document.querySelector("#revoke-all-websites").click();
    await flush(); assert.equal(storage.allWebsites, false);
    assert.ok(window.document.querySelector("#all-websites-status").textContent.includes("incomplete"));
    failRemove = false;
    window.document.querySelector("#revoke-all-websites").click();
    await flush(); assert.equal(granted, false);
    assert.deepEqual(storage.pageOrigins, ["https://kept.example/*"]);
  } finally { window.close(); }
});

test("tabGroups permission is gesture-only, independent, denied/revoked safely and visible in Options", async () => {
  const dom = new JSDOM(readFileSync(new URL("../options.html", import.meta.url), "utf8"), {
    url: "https://extension.test/", runScripts: "outside-only",
  });
  const { window } = dom;
  const storage = { allWebsites: true, pageOrigins: ["https://kept.example/*"] };
  let inGesture = false, granted = false, failRemove = false;
  const requests = [], permissionListeners = {};
  window.chrome = { runtime: { id: "test-extension" }, storage: {
    local: { get: async () => storage, set: async (value) => Object.assign(storage, value) },
    session: { get: async () => ({}) }, onChanged: { addListener() {} },
  }, permissions: {
    contains: async (value) => value.permissions ? granted : true,
    request: async (value) => { assert.equal(inGesture, true); requests.push(value); return granted; },
    remove: async (value) => {
      assert.equal(storage.groupAccess, false);
      assert.deepEqual(Array.from(value.permissions), ["tabGroups"]);
      if (failRemove) throw Error("Chrome denied revoke");
      granted = false; return true;
    },
    onAdded: { addListener(fn) { permissionListeners.added = fn; } },
    onRemoved: { addListener(fn) { permissionListeners.removed = fn; } },
  } };
  const flush = () => new Promise((resolve) => setImmediate(resolve));
  const status = () => window.document.querySelector("#tab-groups-status").textContent;
  try {
    window.eval(readFileSync(new URL("../browser-operations.js", import.meta.url), "utf8"));
    window.eval(readFileSync(new URL("../options.js", import.meta.url), "utf8"));
    await flush(); assert.equal(requests.length, 0); assert.match(status(), /disabled/);
    inGesture = true; window.document.querySelector("#allow-tab-groups").click(); inGesture = false;
    await flush(); assert.match(status(), /denied/); assert.notEqual(storage.groupAccess, true);
    granted = true;
    inGesture = true; window.document.querySelector("#allow-tab-groups").click(); inGesture = false;
    await flush(); assert.equal(storage.groupAccess, true); assert.match(status(), /enabled/);
    assert.deepEqual(Array.from(requests[1].permissions), ["tabGroups"]);
    granted = false; permissionListeners.removed(); await flush(); assert.match(status(), /disabled/);
    granted = true; permissionListeners.added(); await flush(); assert.match(status(), /enabled/);
    failRemove = true; window.document.querySelector("#revoke-tab-groups").click(); await flush();
    assert.equal(storage.groupAccess, false); assert.match(status(), /incomplete/);
    failRemove = false; window.document.querySelector("#revoke-tab-groups").click(); await flush();
    assert.equal(granted, false); assert.match(status(), /disabled/);
    assert.equal(storage.allWebsites, true);
    assert.deepEqual(storage.pageOrigins, ["https://kept.example/*"]);
    assert.equal(requests.length, 2);
  } finally { window.close(); }
});
