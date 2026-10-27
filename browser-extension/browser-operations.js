/* Packaged operations only. CSS selectors select DOM nodes, never execute code. */
(() => {
  const capabilities = ["browser.tabs.create", "browser.tabs.list", "browser.tabs.resolve",
    "browser.tabs.manage", "browser.windows.list", "browser.windows.manage", "browser.navigation.go",
    "browser.navigation.status", "browser.navigation.wait", "browser.groups.list", "browser.groups.manage",
    "browser.bookmarks.query", "browser.bookmarks.manage", "browser.bookmarks.preview_remove_tree", "browser.bookmarks.remove_tree",
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
    if (!text(value, 8192) || !/^https?:\/\/[^/\s\\?#][^\s\\]*$/.test(value) || /[\p{C}\p{Z}]/u.test(value)) return false;
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
    if (["browser.navigation.status", "browser.navigation.wait"].includes(name)) return globalThis.Chat2LocalNavigation.valid(name, args);
    if (name.startsWith("browser.groups.")) return globalThis.Chat2LocalGroups.valid(name, args);
    if (name.startsWith("browser.bookmarks.")) return globalThis.Chat2LocalBookmarks.valid(name, args);
    if (name === "browser.tabs.create") return keys(args, ["url"]) && validUrl(args.url);
    if (name === "browser.tabs.list") return keys(args, ["tab", "limit"])
      && optional(args.tab, validTab) && optional(args.limit, (v) => integer(v, 1, 50));
    if (name === "browser.tabs.resolve") return keys(args, ["tab"]) && validTab(args.tab);
    if (name === "browser.tabs.manage") {
      if (!record(args) || !validTab(args.tab)) return false;
      if (["activate", "close"].includes(args.action)) return keys(args, ["action", "tab"]);
      if (args.action === "set_pinned") return keys(args, ["action", "tab", "pinned"]) && typeof args.pinned === "boolean";
      return args.action === "move" && keys(args, ["action", "tab", "window_id", "index"])
        && integer(args.index, -1, 2 ** 31 - 1) && optional(args.window_id, (v) => integer(v, 0, Number.MAX_SAFE_INTEGER));
    }
    if (name === "browser.windows.list") return keys(args, ["window_id", "limit", "tab_offset", "tab_limit", "tab_snapshot"])
      && optional(args.window_id, (v) => integer(v, 0, Number.MAX_SAFE_INTEGER))
      && optional(args.limit, (v) => integer(v, 1, 50))
      && optional(args.tab_offset, (v) => integer(v, 0, 10000))
      && optional(args.tab_limit, (v) => integer(v, 1, 50))
      && optional(args.tab_snapshot, (v) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v))
      && ((args.tab_offset ?? 0) === 0 || (args.window_id !== undefined && args.tab_snapshot !== undefined))
      && (args.tab_snapshot === undefined || args.window_id !== undefined);
    if (name === "browser.windows.manage") {
      if (!record(args)) return false;
      if (args.action === "create") return keys(args, ["action", "url"]) && optional(args.url, validUrl);
      if (!integer(args.window_id, 0, Number.MAX_SAFE_INTEGER)) return false;
      if (args.action === "focus") return keys(args, ["action", "window_id"]);
      return args.action === "close" && keys(args, ["action", "window_id", "expected_tab_ids"])
        && Array.isArray(args.expected_tab_ids) && args.expected_tab_ids.length <= 10000
        && new Set(args.expected_tab_ids).size === args.expected_tab_ids.length
        && args.expected_tab_ids.every((v) => integer(v, 0, Number.MAX_SAFE_INTEGER));
    }
    if (name === "browser.navigation.go") return record(args) && validTab(args.tab)
      && (args.action === "navigate" ? keys(args, ["action", "tab", "url"]) && validUrl(args.url)
        : ["reload", "back", "forward"].includes(args.action) && keys(args, ["action", "tab"]));
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
  const managedTab = (tab) => ({ ...summary(tab), index: tab.index, active: tab.active, pinned: tab.pinned });
  function windowSummary(window) {
    // A close precondition is always complete; never silently truncate its IDs.
    if (!Array.isArray(window.tabs) || window.tabs.length > 10000) return null;
    return { window_id: window.id, type: window.type, focused: window.focused,
      tab_ids: window.tabs.map((tab) => tab.id), tabs: window.tabs.slice(0, 50).map(managedTab),
      total_tabs: window.tabs.length, tabs_truncated: window.tabs.length > 50 };
  }
  async function normalWindow(id) {
    let window;
    try { window = await chrome.windows.get(id, { populate: true }); }
    catch (error) {
      if (/No window with id/i.test(error?.message ?? "")) return fail("window_not_found");
      throw error;
    }
    return window.type === "normal" ? { ok: true, result: window } : fail("unsupported_window");
  }
  const expired = (expiresAt) => Date.now() >= expiresAt;
  function boundedResult(result) {
    // Leave room for the protocol envelope below the existing 48 KiB limit.
    return new TextEncoder().encode(JSON.stringify(result)).length > 40 * 1024
      ? fail("result_too_large") : { ok: true, result };
  }
  async function manageTab(args, expiresAt) {
    const selected = await resolve(args.tab);
    if (!selected.ok) return selected;
    const tab = selected.result;
    let destination;
    if (args.action === "move") {
      const source = await normalWindow(tab.windowId);
      if (!source.ok) return source;
      destination = args.window_id ?? tab.windowId;
      if (destination !== tab.windowId) {
        const target = await normalWindow(destination);
        if (!target.ok) return target;
      }
    }
    // Preserve every explicit selector after asynchronous window checks.
    const current = await chrome.tabs.get(tab.id);
    if (!matches(current, args.tab) || current.windowId !== tab.windowId) return fail("page_changed");
    if (expired(expiresAt)) return fail("request_expired");
    if (args.action === "close") {
      await chrome.tabs.remove(tab.id);
      return expired(expiresAt) ? fail("request_expired") : { ok: true, result: { action: args.action, tab: managedTab(current) } };
    }
    let moved;
    if (args.action === "move") moved = await chrome.tabs.move(tab.id, { windowId: destination, index: args.index });
    else await chrome.tabs.update(tab.id, args.action === "activate" ? { active: true } : { pinned: args.pinned });
    const after = await chrome.tabs.get(tab.id);
    if (expired(expiresAt)) return fail("request_expired");
    if ((args.action === "activate" && after.active !== true)
      || (args.action === "set_pinned" && after.pinned !== args.pinned)
      || (args.action === "move" && (after.windowId !== destination || after.index !== moved?.index))) return fail("state_not_reached");
    return { ok: true, result: { action: args.action, tab: managedTab(after) } };
  }
  async function listWindows(args, expiresAt) {
    let windows;
    if (args.window_id !== undefined) {
      const selected = await normalWindow(args.window_id);
      if (!selected.ok) return selected;
      windows = [selected.result];
    } else windows = (await chrome.windows.getAll({ populate: true, windowTypes: ["normal"] }))
      .filter((window) => window.type === "normal");
    if (expired(expiresAt)) return fail("request_expired");
    const summaries = [], offset = args.tab_offset ?? 0, limit = args.tab_limit ?? 50;
    for (const window of windows.slice(0, args.limit ?? 20)) {
      const base = windowSummary(window);
      if (!base) return fail("result_too_large");
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify({
        window_id: window.id, tab_ids: base.tab_ids,
      })));
      const snapshot = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
      if (expired(expiresAt)) return fail("request_expired");
      if (args.tab_snapshot !== undefined && args.tab_snapshot !== snapshot) return fail("window_changed");
      if (offset > base.total_tabs) return fail("invalid_arguments");
      const tabs = window.tabs.slice(offset, offset + limit).map(managedTab), next = offset + tabs.length;
      summaries.push({ ...base, tabs, tabs_truncated: offset > 0 || next < base.total_tabs,
        tab_offset: offset, tab_limit: limit, has_more: next < base.total_tabs,
        next_offset: next < base.total_tabs ? next : null, tab_snapshot: snapshot });
    }
    return boundedResult({ windows: summaries, total: windows.length, truncated: windows.length > summaries.length });
  }
  async function manageWindow(args, expiresAt) {
    if (args.action === "create") {
      if (expired(expiresAt)) return fail("request_expired");
      const created = await chrome.windows.create({ type: "normal", ...(args.url === undefined ? {} : { url: args.url }) });
      const observed = await normalWindow(created.id);
      if (!observed.ok) return observed;
      if (expired(expiresAt)) return fail("request_expired");
      const window = windowSummary(observed.result);
      return window ? boundedResult({ action: args.action, window }) : fail("result_too_large");
    }
    const selected = await normalWindow(args.window_id);
    if (!selected.ok) return selected;
    const window = windowSummary(selected.result);
    if (!window) return fail("result_too_large");
    if (args.action === "close") {
      const expected = new Set(args.expected_tab_ids);
      if (window.tab_ids.length !== expected.size || !window.tab_ids.every((id) => expected.has(id))) return fail("window_changed");
      // No asynchronous work between this final snapshot/check and removal.
      const response = boundedResult({ action: args.action, window });
      if (!response.ok) return response;
      if (expired(expiresAt)) return fail("request_expired");
      await chrome.windows.remove(args.window_id);
      return expired(expiresAt) ? fail("request_expired") : response;
    }
    if (expired(expiresAt)) return fail("request_expired");
    await chrome.windows.update(args.window_id, { focused: true });
    const observed = await normalWindow(args.window_id);
    if (!observed.ok) return observed;
    if (expired(expiresAt)) return fail("request_expired");
    if (!observed.result.focused) return fail("state_not_reached");
    const after = windowSummary(observed.result);
    return after ? boundedResult({ action: args.action, window: after }) : fail("result_too_large");
  }
  async function navigate(args, expiresAt) {
    const selected = await resolve(args.tab);
    if (!selected.ok) return selected;
    if (expired(expiresAt)) return fail("request_expired");
    const id = selected.result.id;
    let current;
    try { current = await chrome.tabs.get(id); }
    catch (error) {
      if (/No tab with id|closed/i.test(error?.message ?? "")) return fail("page_changed");
      throw error;
    }
    if (expired(expiresAt)) return fail("request_expired");
    // Revalidate the original ANDed selector, identity and pending destination
    // once. Loading alone is not a race; never wait or substitute a new match.
    if (current.id !== id || !matches(current, args.tab) || current.url !== selected.result.url
      || current.pendingUrl !== selected.result.pendingUrl) return fail("page_changed");
    const observation = globalThis.Chat2LocalNavigation?.begin(current, args);
    try {
      if (args.action === "navigate") await chrome.tabs.update(id, { url: args.url });
      else if (args.action === "reload") await chrome.tabs.reload(id);
      else if (args.action === "back") await chrome.tabs.goBack(id);
      else await chrome.tabs.goForward(id);
    } catch (error) {
      if (observation) globalThis.Chat2LocalNavigation.failed(observation);
      // Chromium uses this same fixed error for both history directions.
      if (["back", "forward"].includes(args.action) && error?.message === "Cannot find a next page in history.") return fail("history_unavailable");
      return fail("browser_operation_failed");
    }
    return expired(expiresAt) ? fail("request_expired") : { ok: true, result: {
      action: args.action, tab_id: id, status: "initiated", navigation_waited: false,
      navigation_id: observation?.id ?? null } };
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
        || !sender.tab || !validTab(message.tab)) return false;
      void (async () => {
        try {
          // Chromium can retain the content script's original sender.url after
          // History API navigation. Bind to the injected document instead of its
          // original route, while requiring the same HTTP(S) origin.
          if (!validUrl(message.expected_url) || !validUrl(sender.url)
            || new URL(sender.url).origin !== new URL(message.expected_url).origin
            || !text(message.expected_document_id, 128) || sender.documentId !== message.expected_document_id
            || sender.documentLifecycle !== "active") {
            sendResponse(fail("page_changed")); return;
          }
          if (!await authorized(message.expected_url)) { sendResponse(fail("permission_denied")); return; }
          const tab = await chrome.tabs.get(sender.tab.id);
          if (tab.url !== message.expected_url || tab.pendingUrl || !matches(tab, message.tab)) {
            sendResponse(fail("page_changed"));
          } else sendResponse({ ok: true });
        } catch { sendResponse(fail("browser_operation_failed")); }
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
        expected_url: tab.url, expected_document_id: documentId, expires_at: expiresAt,
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
      if (request.capability === "browser.tabs.manage") return await manageTab(args, request.expires_at);
      if (request.capability === "browser.windows.list") return await listWindows(args, request.expires_at);
      if (request.capability === "browser.windows.manage") return await manageWindow(args, request.expires_at);
      if (request.capability === "browser.navigation.go") return await navigate(args, request.expires_at);
      if (["browser.navigation.status", "browser.navigation.wait"].includes(request.capability)) return await globalThis.Chat2LocalNavigation.execute(request.capability, args, request.expires_at);
      if (request.capability.startsWith("browser.groups.")) return await globalThis.Chat2LocalGroups.execute(request.capability, args, request.expires_at);
      if (request.capability.startsWith("browser.bookmarks.")) return await globalThis.Chat2LocalBookmarks.execute(request.capability, args, request.expires_at);
      return await pageOperation(request.capability, args, request.expires_at);
    } catch { return fail("browser_operation_failed"); }
  }
  globalThis.Chat2LocalBrowser = { capabilities, execute, installAuthorizationHandler, pagePattern, allWebsiteOrigins };
  const shared = { fail, keys, integer, text, validTab, summary, managedTab, matches, resolve, normalWindow, boundedResult };
  globalThis.Chat2LocalNavigation?.configure(shared);
  globalThis.Chat2LocalGroups?.configure(shared);
  globalThis.Chat2LocalBookmarks?.configure(shared);
})();
