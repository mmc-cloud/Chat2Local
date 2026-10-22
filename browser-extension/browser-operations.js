/* Packaged operations only. Neither source code nor selectors are evaluated. */
(() => {
  const capabilities = ["browser.tabs.create", "browser.tabs.list", "browser.tabs.resolve",
    "browser.page.inspect", "browser.page.click", "browser.page.fill"];
  const targetKeys = ["role", "name", "aria_label", "label", "placeholder", "href", "text", "id"];
  const fail = (code, details) => ({ ok: false, error: { code, ...(details ? { details } : {}) } });
  const record = (value) => value && typeof value === "object" && !Array.isArray(value);
  const keys = (value, allowed) => record(value) && Object.keys(value).every((key) => allowed.includes(key));
  const integer = (value, min, max) => Number.isInteger(value) && value >= min && value <= max;
  // Match Python / JSON Schema character counts, including non-BMP characters.
  const text = (value, max) => typeof value === "string" && value.length > 0 && Array.from(value).length <= max;
  const optional = (value, validate) => value === undefined || validate(value);
  function validUrl(value) {
    if (!text(value, 8192) || !/^https?:\/\/[^\s\\]+$/.test(value) || /[\p{C}\p{Z}]/u.test(value)) return false;
    try { const url = new URL(value); return Boolean(url.hostname) && !url.username && !url.password && url.port !== "0"; }
    catch { return false; }
  }
  function validTab(tab) {
    return keys(tab, ["tab_id", "url", "title"]) && Object.keys(tab).length > 0
      && optional(tab.tab_id, (v) => integer(v, 0, Number.MAX_SAFE_INTEGER))
      && optional(tab.url, (v) => text(v, 8192)) && optional(tab.title, (v) => text(v, 512));
  }
  function validTarget(target) {
    return keys(target, targetKeys) && Object.keys(target).length > 0
      && Object.entries(target).every(([key, value]) => text(value, key === "href" ? 8192 : key === "role" ? 64 : 256));
  }
  function validArguments(name, args) {
    if (name === "browser.tabs.create") return keys(args, ["url"]) && validUrl(args.url);
    if (name === "browser.tabs.list") return keys(args, ["tab", "limit"])
      && optional(args.tab, validTab) && optional(args.limit, (v) => integer(v, 1, 50));
    if (name === "browser.tabs.resolve") return keys(args, ["tab"]) && validTab(args.tab);
    const inspect = name === "browser.page.inspect", fill = name === "browser.page.fill";
    return keys(args, ["tab", "target", "wait_ms", ...(inspect ? ["limit"] : []), ...(fill ? ["value"] : [])])
      && validTab(args.tab) && (inspect ? optional(args.target, validTarget) : validTarget(args.target))
      && optional(args.wait_ms, (v) => integer(v, 0, 5000))
      && (!inspect || optional(args.limit, (v) => integer(v, 1, 20)))
      && (!fill || (typeof args.value === "string" && Array.from(args.value).length <= 8192));
  }
  const summary = (tab) => ({ tab_id: tab.id, window_id: tab.windowId,
    title: (tab.title || "").slice(0, 200), url: (tab.url || "").slice(0, 2048) });
  const matches = (tab, selector) => !selector || (selector.tab_id === undefined || tab.id === selector.tab_id)
    && (selector.url === undefined || tab.url === selector.url)
    && (selector.title === undefined || tab.title === selector.title);
  async function findTabs(selector) {
    if (selector?.tab_id !== undefined) {
      try { const tab = await chrome.tabs.get(selector.tab_id); return matches(tab, selector) ? [tab] : []; }
      catch { return []; }
    }
    return (await chrome.tabs.query({})).filter((tab) => matches(tab, selector));
  }
  async function resolve(selector) {
    const tabs = await findTabs(selector);
    if (!tabs.length) return fail("tab_not_found");
    if (tabs.length !== 1) return fail("ambiguous_tab", { candidates: tabs.slice(0, 5).map(summary) });
    return { ok: true, result: tabs[0] };
  }
  function pagePattern(value) {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.hostname.includes("*")
      || url.hostname === "chromewebstore.google.com"
      || (url.hostname === "chrome.google.com" && url.pathname.startsWith("/webstore"))) return null;
    return `${url.protocol}//${url.hostname}/*`;
  }
  async function authorized(url) {
    const pattern = pagePattern(url);
    if (!pattern) return false;
    // Explicit website opt-in is also required for loopback, whose bridge host
    // permission is already granted at install time for network connectivity.
    const { pageOrigins = [] } = await chrome.storage.local.get("pageOrigins");
    return Array.isArray(pageOrigins) && pageOrigins.includes(pattern)
      && await chrome.permissions.contains({ origins: [pattern] });
  }
  function installAuthorizationHandler() {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (message?.type !== "chat2local.dom.authorize" || sender.id !== chrome.runtime.id || sender.frameId !== 0
        || !sender.tab || sender.url !== message.expected_url || !validTab(message.tab)) return false;
      void (async () => {
        try {
          if (!await authorized(sender.url)) { sendResponse({ ok: false }); return; }
          const tab = await chrome.tabs.get(sender.tab.id);
          if (tab.url !== sender.url || tab.pendingUrl || !matches(tab, message.tab)) {
            sendResponse(fail("page_changed"));
          } else sendResponse({ ok: true });
        } catch { sendResponse({ ok: false }); }
      })();
      return true;
    });
  }
  async function pageOperation(name, args, expiresAt) {
    const selected = await resolve(args.tab);
    if (!selected.ok) return selected;
    const tab = selected.result;
    if (!tab.url || !pagePattern(tab.url)) return fail("unsupported_page");
    if (!await authorized(tab.url)) return fail("permission_denied");
    if (tab.pendingUrl) return fail("page_changed");
    if (Date.now() >= expiresAt) return fail("request_expired");
    let documentId;
    try {
      const injected = await chrome.scripting.executeScript({
        target: { tabId: tab.id, frameIds: [0] }, files: ["content-script.js"],
        world: "ISOLATED", injectImmediately: true,
      });
      documentId = injected.find((item) => item.frameId === 0)?.documentId;
      if (!documentId) return fail("injection_failed");
    } catch {
      try {
        const current = await chrome.tabs.get(tab.id);
        if (current.url !== tab.url || current.pendingUrl || !matches(current, args.tab)) return fail("page_changed");
        if (!await authorized(current.url)) return fail("permission_denied");
      } catch { return fail("tab_not_found"); }
      return fail("injection_failed");
    }
    try {
      if (!await authorized(tab.url)) return fail("permission_denied");
      // Read after the asynchronous permission check, and preserve every caller
      // condition rather than treating an unchanged URL as a stable selection.
      const current = await chrome.tabs.get(tab.id);
      if (current.url !== tab.url || current.pendingUrl || !matches(current, args.tab)) return fail("page_changed");
      if (Date.now() >= expiresAt) return fail("request_expired");
      const result = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2local.dom", operation: name.split(".").at(-1), arguments: args,
        expected_url: tab.url, expires_at: expiresAt,
      }, { documentId });
      if (!record(result) || typeof result.ok !== "boolean") return fail("injection_failed");
      return result.ok ? { ok: true, result: { tab: summary(current), ...result.result } } : result;
    } catch {
      try { await chrome.tabs.get(tab.id); } catch { return fail("tab_not_found"); }
      return fail("page_changed"); // Never replay an action after losing its document.
    }
  }
  async function execute(request) {
    if (!capabilities.includes(request.capability)) return fail("unknown_capability");
    if (!validArguments(request.capability, request.arguments)) return fail("invalid_arguments");
    if (request.expires_at <= Date.now()) return fail("request_expired");
    const args = request.arguments;
    try {
      if (request.capability === "browser.tabs.create") {
        const tab = await chrome.tabs.create({ url: args.url });
        return { ok: true, result: { tab_id: tab.id, window_id: tab.windowId, url: args.url } };
      }
      if (request.capability === "browser.tabs.list") {
        const tabs = await findTabs(args.tab), limit = args.limit ?? 20;
        if (args.tab && !tabs.length) return fail("tab_not_found");
        return { ok: true, result: { tabs: tabs.slice(0, limit).map(summary), total: tabs.length, truncated: tabs.length > limit } };
      }
      if (request.capability === "browser.tabs.resolve") {
        const result = await resolve(args.tab);
        return result.ok ? { ok: true, result: summary(result.result) } : result;
      }
      return await pageOperation(request.capability, args, request.expires_at);
    } catch { return fail("browser_operation_failed"); }
  }
  globalThis.Chat2LocalBrowser = { capabilities, execute, installAuthorizationHandler, pagePattern };
})();
