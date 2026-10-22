import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
const source = readFileSync(new URL("../content-script.js", import.meta.url), "utf8");
export function page(html, { url = "https://test.example/", authorize = async () => ({ ok: true }) } = {}) {
  const dom = new JSDOM(html, { url, runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  let listener;
  window.chrome = { runtime: { id: "test-extension", sendMessage: authorize,
    onMessage: { addListener(fn) { listener = fn; } } } };
  // jsdom has no layout engine. Visibility logic still runs the real style/
  // ancestry checks; only rectangle presence is supplied by this test adapter.
  window.HTMLElement.prototype.getClientRects = function () { return this.isConnected ? [{ width: 100, height: 20 }] : []; };
  window.eval(source);
  const dispatch = (message, sender = { id: "test-extension" }) => new Promise((resolve) => {
    if (!listener(message, sender, resolve)) resolve({ ignored: true });
  });
  const call = (operation, args = {}, extra = {}) => dispatch({ type: "chat2local.dom", operation,
    arguments: args, expected_url: url, expires_at: Date.now() + 10000, ...extra });
  return { dom, window, document: window.document, call, dispatch };
}
