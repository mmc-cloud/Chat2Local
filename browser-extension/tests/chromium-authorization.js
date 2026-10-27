/* Real MV3 worker used only by chromium_authorization.py's disposable extension. */
importScripts("browser-safety.js", "browser-operations.js");
const evidence = [], checks = [], pages = [];
let phase;
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === "chat2local.dom.authorize") evidence.push({ phase,
    sender_url: sender.url, sender_tab_url: sender.tab?.url, document_id: sender.documentId,
    document_lifecycle: sender.documentLifecycle, frame_id: sender.frameId, expected_url: message.expected_url });
  return false;
});
Chat2LocalBrowser.installAuthorizationHandler();
const check = (name, passed, result) => checks.push({ name, passed, result });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function loaded(id) {
  for (let attempt = 0; attempt < 200; attempt++) {
    const tab = await chrome.tabs.get(id);
    if (tab.status === "complete" && !tab.pendingUrl) return;
    await delay(20);
  }
  throw Error("fixture load timed out");
}
chrome.runtime.onInstalled.addListener(() => { void (async () => {
  try {
    await chrome.storage.local.set({ allWebsites: true, pageOrigins: [] });
    const tab = await chrome.tabs.create({ url: base + "/analytics" });
    await loaded(tab.id);
    const read = async (name) => {
      phase = name;
      const [snapshot] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => location.href });
      pages.push({ phase, location_href: snapshot.result, tab_url: (await chrome.tabs.get(tab.id)).url,
        document_id: snapshot.documentId });
      const result = await Chat2LocalBrowser.execute({ capability: "browser.page.read",
        arguments: { tab: { tab_id: tab.id } }, expires_at: Date.now() + 10000 });
      check(name, result.ok === true, result.ok ? { ok: true, url: result.result.page.url } : result);
    };
    await read("initial");
    for (const method of ["pushState", "replaceState"]) {
      const url = base + "/" + method + "?range=week#chart";
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "MAIN",
        func: (method, url) => history[method](null, "", url), args: [method, url] });
      await delay(100);
      await read(method);
    }
    await chrome.tabs.reload(tab.id);
    await delay(100);
    await loaded(tab.id);
    await read("reload");
    const originalDocument = pages[0].document_id, reloaded = pages.at(-1);
    check("SPA preserves document identity", pages[1].document_id === originalDocument && pages[2].document_id === originalDocument);
    check("reload replaces document at the same URL", reloaded.document_id !== originalDocument
      && reloaded.location_href === pages[2].location_href);
    // Use the actual native tab ID in test-only messages, without a Chrome API stub.
    const authorizeCurrent = async (expectedUrl, expectedDocument) => {
      const [injected] = await chrome.scripting.executeScript({ target: { tabId: tab.id },
        func: (expected_url, expected_document_id, tab_id) => chrome.runtime.sendMessage({ type: "chat2local.dom.authorize",
          expected_url, expected_document_id, tab: { tab_id } }), args: [expectedUrl, expectedDocument, tab.id] });
      return injected.result;
    };
    phase = "stale document authorization";
    let result = await authorizeCurrent(reloaded.location_href, originalDocument);
    check(phase, result.error?.code === "page_changed", result);
    phase = "revoked consent authorization";
    await chrome.storage.local.set({ allWebsites: false });
    result = await authorizeCurrent(reloaded.location_href, reloaded.document_id);
    check(phase, result.error?.code === "permission_denied", result);
    await chrome.storage.local.set({ allWebsites: true });
    await chrome.tabs.update(tab.id, { url: base.replace("127.0.0.1", "localhost") + "/cross-origin" });
    await delay(100);
    await loaded(tab.id);
    await read("authorized cross-origin new document");
    phase = "cross-origin stale URL authorization";
    result = await authorizeCurrent(reloaded.location_href, pages.at(-1).document_id);
    check(phase, result.error?.code === "page_changed", result);
  } catch (error) { check("harness", false, String(error)); }
  await fetch(base + "/report", { method: "POST", body: JSON.stringify({
    browser: navigator.userAgent, failed: checks.filter((item) => !item.passed).length, checks, pages, evidence,
  }) });
})(); });
