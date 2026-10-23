/* Packaged operations only. CSS selectors select DOM nodes, never execute code. */
(() => {
  const capabilities = ["browser.tabs.create", "browser.tabs.list", "browser.tabs.resolve",
    "browser.page.read", "browser.page.inspect", "browser.page.dom.inspect", "browser.page.click", "browser.page.fill",
    "browser.page.double_click", "browser.page.hover", "browser.page.focus", "browser.page.select",
    "browser.page.set_checked", "browser.page.scroll", "browser.page.press_key", "browser.page.wait_for"];
  const targetKeys = ["ref", "role", "name", "aria_label", "label", "placeholder", "href", "text", "id", "css"];
  const allWebsiteOrigins = ["http://*/*", "https://*/*"];
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
      && Object.entries(target).every(([key, value]) => key === "ref" ? text(value, 128) && /^el_[A-Za-z0-9_-]+$/.test(value)
        : text(value, key === "css" ? 1024 : key === "href" ? 8192 : key === "role" ? 64 : 256));
  }
  function validOption(option) {
    return keys(option, ["value", "label", "index"]) && Object.keys(option).length > 0
      && optional(option.value, (v) => typeof v === "string" && Array.from(v).length <= 256)
      && optional(option.label, (v) => typeof v === "string" && Array.from(v).length <= 256)
      && optional(option.index, (v) => integer(v, 0, 9999));
  }
  function validArguments(name, args) {
    if (name === "browser.tabs.create") return keys(args, ["url"]) && validUrl(args.url);
    if (name === "browser.tabs.list") return keys(args, ["tab", "limit"])
      && optional(args.tab, validTab) && optional(args.limit, (v) => integer(v, 1, 50));
    if (name === "browser.tabs.resolve") return keys(args, ["tab"]) && validTab(args.tab);
    if (name === "browser.page.read") return keys(args, ["tab", "cursor", "max_chars"]) && validTab(args.tab)
      && optional(args.cursor, (v) => text(v, 512) && /^[A-Za-z0-9_-]+$/.test(v))
      && optional(args.max_chars, (v) => integer(v, 1000, 12000));
    if (name === "browser.page.dom.inspect") return keys(args, ["tab", "css", "text", "tag", "scope", "limit", "depth", "wait_ms"])
      && validTab(args.tab) && [args.css, args.text, args.tag].some((value) => value !== undefined)
      && optional(args.css, (v) => text(v, 1024)) && optional(args.scope, (v) => text(v, 1024))
      && optional(args.text, (v) => text(v, 256))
      && optional(args.tag, (v) => text(v, 64) && /^[a-zA-Z][a-zA-Z0-9-]*$/.test(v))
      && optional(args.limit, (v) => integer(v, 1, 10)) && optional(args.depth, (v) => integer(v, 0, 3))
      && optional(args.wait_ms, (v) => integer(v, 0, 5000));
    if (name === "browser.page.wait_for") return keys(args, ["tab", "target", "state", "timeout_ms"])
      && validTab(args.tab) && validTarget(args.target)
      && optional(args.state, (v) => ["attached", "detached", "visible", "hidden", "enabled", "disabled", "actionable"].includes(v))
      && optional(args.timeout_ms, (v) => integer(v, 0, 10000));
    if (name === "browser.page.scroll") return keys(args, ["tab", "container", "target", "delta_x", "delta_y", "wait_ms"])
      && validTab(args.tab) && ["page", "element"].includes(args.container)
      && (args.container === "element" ? validTarget(args.target) : args.target === undefined)
      && optional(args.delta_x, (v) => integer(v, -100000, 100000))
      && optional(args.delta_y, (v) => integer(v, -100000, 100000))
      && ((args.delta_x ?? 0) !== 0 || (args.delta_y ?? 0) !== 0)
      && optional(args.wait_ms, (v) => integer(v, 0, 5000));
    const inspect = name === "browser.page.inspect", fill = name === "browser.page.fill";
    const select = name === "browser.page.select", checked = name === "browser.page.set_checked", key = name === "browser.page.press_key";
    return keys(args, ["tab", "target", "wait_ms", ...(inspect ? ["limit", "select_options"] : []), ...(fill ? ["value"] : []),
      ...(select ? ["options"] : []), ...(checked ? ["checked"] : []), ...(key ? ["key", "modifiers"] : [])])
      && validTab(args.tab) && (inspect ? optional(args.target, validTarget) : validTarget(args.target))
      && optional(args.wait_ms, (v) => integer(v, 0, 5000))
      && (!inspect || optional(args.limit, (v) => integer(v, 1, 20)))
      && (!inspect || optional(args.select_options, (v) => validTarget(args.target) && keys(v, ["offset", "limit"])
        && optional(v.offset, (n) => integer(n, 0, 9999)) && optional(v.limit, (n) => integer(n, 1, 50))))
      && (!fill || (typeof args.value === "string" && Array.from(args.value).length <= 8192))
      && (!select || (Array.isArray(args.options) && args.options.length <= 50 && args.options.every(validOption)))
      && (!checked || typeof args.checked === "boolean")
      && (!key || (text(args.key, 13) && /^(Enter|Tab|Escape|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Home|End|PageUp|PageDown|Backspace|Delete|Space|[a-zA-Z0-9])$/.test(args.key)
        && optional(args.modifiers, (v) => Array.isArray(v) && v.length <= 4 && new Set(v).size === v.length
          && v.every((item) => ["Alt", "Control", "Meta", "Shift"].includes(item)))));
  }
  const summary = (tab) => ({ tab_id: tab.id, window_id: tab.windowId,
    title: Chat2LocalSafety.text(tab.title, 200).text, url: Chat2LocalSafety.url(tab.url).url });
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
    const { pageOrigins = [], allWebsites = false } = await chrome.storage.local.get(["pageOrigins", "allWebsites"]);
    if (allWebsites === true && await chrome.permissions.contains({ origins: allWebsiteOrigins })) return true;
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
  async function paginateRead(snapshot, args, documentId, url) {
    // Action refs may renew without any readable content changing. Exclude them
    // only from the content version; return the original blocks with current refs.
    const content = { ...snapshot, blocks: snapshot.blocks.map(({ ref, ...block }) => block) };
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify({ url, snapshot: content })));
    const version = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    let offset = 0;
    if (args.cursor) {
      let cursor;
      try {
        cursor = JSON.parse(atob(args.cursor.replace(/-/g, "+").replace(/_/g, "/")));
        if (!keys(cursor, ["document", "version", "offset"]) || typeof cursor.document !== "string"
          || !/^[0-9a-f]{64}$/.test(cursor.version) || !integer(cursor.offset, 0, 10000)) return fail("invalid_cursor");
      } catch { return fail("invalid_cursor"); }
      if (cursor.document !== documentId || cursor.version !== version) return fail("page_changed");
      if (cursor.offset > snapshot.blocks.length) return fail("invalid_cursor");
      offset = cursor.offset;
    }
    const blocks = [], maxChars = args.max_chars ?? 8000;
    const metadataBytes = new TextEncoder().encode(JSON.stringify({ page: snapshot.page, overview: snapshot.overview,
      tab: { url: snapshot.page.url, title: snapshot.page.title } })).length;
    const byteLimit = Math.min(32 * 1024, 40 * 1024 - metadataBytes - 1024);
    let chars = 0, bytes = 0;
    for (const block of snapshot.blocks.slice(offset)) {
      const length = Array.from(block.text || "").length;
      const size = new TextEncoder().encode(JSON.stringify(block)).length + 1;
      if (chars + length > maxChars || bytes + size > byteLimit) break;
      blocks.push(block); chars += length; bytes += size;
    }
    if (!blocks.length && offset < snapshot.blocks.length) return fail("result_too_large");
    const nextOffset = offset + blocks.length, hasMore = nextOffset < snapshot.blocks.length;
    const cursor = hasMore ? btoa(JSON.stringify({ document: documentId, version, offset: nextOffset }))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "") : null;
    return { ok: true, result: { page: snapshot.page, overview: snapshot.overview, blocks,
      offset, total_blocks: snapshot.blocks.length, has_more: hasMore, next_cursor: cursor,
      truncated: hasMore || snapshot.truncated, content_truncated: snapshot.truncated, content_is_untrusted: true } };
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
        target: { tabId: tab.id, frameIds: [0] }, files: ["browser-safety.js", "content-script.js"],
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
        type: "chat2local.dom", operation: name === "browser.page.dom.inspect" ? "dom.inspect" : name.split(".").at(-1), arguments: args,
        expected_url: tab.url, expires_at: expiresAt,
      }, { documentId });
      if (!record(result) || typeof result.ok !== "boolean") return fail("injection_failed");
      if (result.ok && name === "browser.page.read") {
        const read = await paginateRead(result.result, args, documentId, tab.url);
        if (!read.ok) return read;
        // Hashing yields: recheck consent and the full selector before returning data.
        if (!await authorized(tab.url)) return fail("permission_denied");
        const finalTab = await chrome.tabs.get(tab.id);
        if (finalTab.url !== tab.url || finalTab.pendingUrl || !matches(finalTab, args.tab)) return fail("page_changed");
        if (Date.now() >= expiresAt) return fail("request_expired");
        return { ok: true, result: { tab: { ...summary(finalTab), url: read.result.page.url, title: read.result.page.title }, ...read.result } };
      }
      if (result.ok && !["browser.page.inspect", "browser.page.dom.inspect", "browser.page.click", "browser.page.fill"].includes(name)) {
        // Phase 3 results are observations, not a promise of business success.
        // Recheck consent/selection even after an action; failure here never replays it.
        const completed = name === "browser.page.press_key" ? { events_dispatched: { keydown: true, keyup: true } } : undefined;
        if (!await authorized(tab.url)) return fail("permission_denied", completed);
        const finalTab = await chrome.tabs.get(tab.id);
        if (finalTab.url !== tab.url || finalTab.pendingUrl || !matches(finalTab, args.tab)) return fail("page_changed", completed);
        if (Date.now() >= expiresAt) return fail("request_expired", completed);
        return { ok: true, result: { tab: summary(finalTab), ...result.result } };
      }
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
        return { ok: true, result: { tab_id: tab.id, window_id: tab.windowId, url: Chat2LocalSafety.url(args.url).url } };
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
  globalThis.Chat2LocalBrowser = { capabilities, execute, installAuthorizationHandler, pagePattern, allWebsiteOrigins };
})();
