import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
const source = readFileSync(new URL("../content-script.js", import.meta.url), "utf8");
export function page(html, { url = "https://test.example/", authorize = async () => ({ ok: true }) } = {}) {
  const dom = new JSDOM(html, { url, runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  window.TextEncoder = TextEncoder;
  let listener;
  window.chrome = { runtime: { id: "test-extension", sendMessage: authorize,
    onMessage: { addListener(fn) { listener = fn; } } } };
  // jsdom has no layout/hit testing. Give nodes separate deterministic boxes;
  // geometry/occlusion cases additionally run in real headless Chromium.
  const boxes = new WeakMap();
  let sequence = 0;
  Object.defineProperties(window.document.documentElement, { clientWidth: { value: 1024, configurable: true }, clientHeight: { value: 1000000, configurable: true } });
  window.HTMLElement.prototype.getClientRects = function () {
    if (!this.isConnected) return [];
    if (!boxes.has(this)) {
      const top = sequence++ * 25;
      boxes.set(this, { x: 0, y: top, left: 0, top, right: 100, bottom: top + 20, width: 100, height: 20 });
    }
    return [boxes.get(this)];
  };
  window.document.elementFromPoint = (x, y) => [...window.document.querySelectorAll("*")].find((node) => {
    const box = boxes.get(node);
    return box && x >= box.left && x < box.right && y >= box.top && y < box.bottom;
  }) ?? null;
  window.eval(readFileSync(new URL("../browser-safety.js", import.meta.url), "utf8"));
  window.eval(source);
  const dispatch = (message, sender = { id: "test-extension" }) => new Promise((resolve) => {
    if (!listener(message, sender, resolve)) resolve({ ignored: true });
  });
  const call = (operation, args = {}, extra = {}) => dispatch({ type: "chat2local.dom", operation,
    arguments: args, expected_url: url, expires_at: Date.now() + 10000, ...extra });
  return { dom, window, document: window.document, call, dispatch };
}
