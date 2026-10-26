/* Native bookmarks only. Optional consent, bounded reads, no mutation retries. */
(() => {
  let shared, signingKey;
  const previews = new Map(), MAX_NODES = 10000, MAX_DEPTH = 64, MAX_BYTES = 2 * 1024 * 1024;
  const id = (v) => typeof v === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(v);
  const previewId = (v) => typeof v === "string" && /^bmk_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
  const optional = (v, check) => v === undefined || check(v);
  const folder = (node) => node.url === undefined;
  const queryFields = { roots: ["offset", "limit", "snapshot"], get: ["bookmark_id"],
    children: ["folder_id", "offset", "limit", "snapshot"], subtree: ["folder_id", "depth", "offset", "limit", "snapshot"],
    search: ["query", "title", "url", "offset", "limit", "snapshot"], recent: ["offset", "limit", "snapshot"] };
  const manageFields = { create: ["kind", "parent_id", "title", "url", "index"], update: ["bookmark_id", "title", "url"],
    move: ["bookmark_id", "parent_id", "index"], remove: ["bookmark_id"] };
  const urlPattern = /^(?:(?:https?|ftp|file|chrome|chrome-extension):\/\/[^\s\\]+|about:blank)$/;
  function validURL(value) {
    if (typeof value !== "string" || Array.from(value).length > 8192 || !urlPattern.test(value)
      || /[\p{C}\p{Z}]/u.test(value)) return false;
    try {
      const parsed = new URL(value);
      if (!["about:", "file:"].includes(parsed.protocol) && !/^[a-z-]+:\/\/[^/?#]/.test(value)) return false;
      return !parsed.username && !parsed.password && !value.split("://")[1]?.split(/[/?#]/)[0].includes("@") && parsed.port !== "0"
        && (!["chrome:", "chrome-extension:", "file:"].includes(parsed.protocol) || !parsed.port)
        && (parsed.protocol === "about:" || parsed.protocol === "file:" ? parsed.pathname.startsWith("/") || value === "about:blank" : Boolean(parsed.hostname));
    } catch { return false; }
  }
  function canonicalURL(value) {
    const parsed = new URL(value);
    // Chrome registers these as standard browser schemes. WHATWG URL in an
    // extension worker treats them as non-special and leaves an empty path.
    if (["chrome:", "chrome-extension:"].includes(parsed.protocol)) {
      parsed.hostname = parsed.hostname.toLowerCase();
      if (!parsed.pathname) parsed.pathname = "/";
    }
    return parsed.href;
  }
  function valid(name, args) {
    const { keys, integer, text } = shared;
    if (name === "browser.bookmarks.preview_remove_tree") return keys(args, ["folder_id", "examples_limit"])
      && id(args.folder_id) && optional(args.examples_limit, (v) => integer(v, 1, 20));
    if (name === "browser.bookmarks.remove_tree") return keys(args, ["folder_id", "preview_id"]) && id(args.folder_id) && previewId(args.preview_id);
    const query = name === "browser.bookmarks.query", fields = query ? queryFields : manageFields;
    if (!args || !Object.hasOwn(fields, args.action) || !keys(args, ["action", ...fields[args.action]])) return false;
    if (!optional(args.bookmark_id, id) || !optional(args.folder_id, id) || !optional(args.parent_id, id)) return false;
    if (query) return (args.action !== "get" || id(args.bookmark_id))
      && (!["children", "subtree"].includes(args.action) || id(args.folder_id))
      && (args.action !== "search" || ["query", "title", "url"].some((key) => args[key] !== undefined))
      && optional(args.query, (v) => text(v, 512)) && optional(args.title, (v) => text(v, 512)) && optional(args.url, (v) => text(v, 8192))
      && optional(args.depth, (v) => integer(v, 0, 5)) && optional(args.offset, (v) => integer(v, 0, MAX_NODES))
      && optional(args.limit, (v) => integer(v, 1, 50)) && optional(args.snapshot, (v) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v))
      && (!(args.offset > 0) || args.snapshot !== undefined);
    return (args.action === "create" ? ["bookmark", "folder"].includes(args.kind) && id(args.parent_id) && args.title !== undefined
      && (args.kind === "bookmark" ? args.url !== undefined : args.url === undefined) : id(args.bookmark_id))
      && (args.action !== "move" || id(args.parent_id))
      && (args.action !== "update" || args.title !== undefined || args.url !== undefined)
      && optional(args.title, (v) => typeof v === "string" && Array.from(v).length <= 512)
      && optional(args.url, validURL) && optional(args.index, (v) => integer(v, 0, 2 ** 31 - 1));
  }
  class Failure extends Error { constructor(code) { super(); this.code = code; } }
  const stop = (code) => { throw new Failure(code); };
  async function gate(expiresAt) {
    const { bookmarkAccess = false } = await chrome.storage.local.get("bookmarkAccess");
    if (bookmarkAccess !== true || !await chrome.permissions.contains({ permissions: ["bookmarks"] })) stop("bookmarks_permission_denied");
    if (Date.now() >= expiresAt) stop("request_expired");
  }
  async function get(bookmarkId) {
    try {
      const nodes = await chrome.bookmarks.get(bookmarkId);
      if (!nodes.length) return null;
      if (nodes.length !== 1 || nodes[0].id !== bookmarkId) stop("bookmark_changed");
      return nodes[0];
    } catch (error) {
      if (/Can't find bookmark|No bookmark|Bookmark id is invalid|Invalid bookmark id|No node/i.test(error?.message ?? "")) return null;
      throw error;
    }
  }
  async function requireNode(bookmarkId, mustBeFolder = false) {
    const node = await get(bookmarkId);
    if (!node) stop("bookmark_not_found");
    if (mustBeFolder && !folder(node)) stop("bookmark_not_folder");
    return node;
  }
  // No hard-coded root/permanent IDs. Legacy Chrome lacks folderType, so also
  // recognize permanent folders through their direct relationship to the root.
  async function writable(node, container = false, forbiddenAncestor) {
    if (container && !folder(node)) stop("bookmark_not_folder");
    let current = node;
    const seen = new Set();
    for (let depth = 0; depth <= MAX_DEPTH; depth++) {
      if (seen.has(current.id)) stop("bookmark_changed");
      seen.add(current.id);
      if (current.id === forbiddenAncestor) stop("bookmark_invalid_move");
      if (current.unmodifiable !== undefined || current.folderType === "managed") stop("bookmark_protected");
      if (depth === 0 && (current.parentId === undefined || !container && current.folderType !== undefined)) stop("bookmark_protected");
      if (current.parentId === undefined) return;
      const parent = await requireNode(current.parentId, true);
      if (depth === 0 && !container && parent.parentId === undefined) stop("bookmark_protected");
      current = parent;
    }
    stop("bookmark_traversal_limit");
  }
  const state = (node) => [node.id, folder(node), node.parentId ?? null, node.index ?? null, node.title, node.url ?? null,
    node.dateAdded ?? null, node.dateGroupModified ?? null, node.folderType ?? null, node.unmodifiable ?? null, node.syncing ?? null];
  const same = (a, b) => JSON.stringify(state(a)) === JSON.stringify(state(b));
  function displayURL(value) {
    const filtered = Chat2LocalSafety.url(value);
    if (filtered.url !== null || typeof value !== "string" || value.length > 8192) return filtered;
    try {
      const parsed = new URL(value);
      if (parsed.href === "about:blank") return { url: "about:blank", truncated: false };
      if (!["ftp:", "file:", "chrome:", "chrome-extension:"].includes(parsed.protocol)) return filtered;
      // Reuse the same path/secret filtering without exposing credentials,
      // queries or fragments. Only known non-executable schemes are restored.
      const surrogate = new URL("https://bookmark.invalid/");
      surrogate.host = parsed.host || "bookmark.invalid"; surrogate.pathname = parsed.pathname;
      const safe = Chat2LocalSafety.url(surrogate.href);
      if (!safe.url) return safe;
      const result = new URL(safe.url), host = parsed.host ? result.host : "";
      const url = `${parsed.protocol}//${host}${result.pathname}`;
      return { url: url.length <= 2048 ? url : `${parsed.protocol}//${host}/`, truncated: safe.truncated || url.length > 2048 };
    } catch { return filtered; }
  }
  function display(node, depth = 0, omitted = folder(node)) {
    const title = Chat2LocalSafety.text(node.title, 512), safe = folder(node) ? { url: null, truncated: false } : displayURL(node.url);
    return { bookmark_id: node.id, type: folder(node) ? "folder" : "bookmark", title: title.text,
      title_truncated: title.truncated, url: safe.url, url_truncated: safe.truncated, url_omitted: !folder(node) && safe.url === null,
      parent_id: node.parentId ?? null, index: node.index ?? null, date_added: node.dateAdded ?? null,
      date_group_modified: node.dateGroupModified ?? null, date_last_used: node.dateLastUsed ?? null,
      folder_type: node.folderType ?? null, unmodifiable: node.unmodifiable ?? null, syncing: node.syncing ?? null,
      depth, children_omitted: omitted };
  }
  async function fingerprint(value) {
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    if (bytes.length > MAX_BYTES) stop("bookmark_traversal_limit");
    // Keyed query snapshots do not expose guessable hashes of sensitive URLs.
    signingKey ??= crypto.subtle.generateKey({ name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const digest = await crypto.subtle.sign("HMAC", await signingKey, bytes);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  async function children(node) {
    if (!folder(node)) stop("bookmark_not_folder");
    const rows = await chrome.bookmarks.getChildren(node.id);
    if (rows.length > MAX_NODES) stop("bookmark_traversal_limit");
    if (rows.some((child, index) => child.parentId !== node.id || child.index !== index)) stop("bookmark_changed");
    return rows;
  }
  async function query(args, expiresAt) {
    let rows = [], depthLimited = false;
    if (args.action === "get") rows = [{ node: await requireNode(args.bookmark_id), depth: 0 }];
    else if (args.action === "roots") {
      let roots;
      // Chrome 145+ exposes the root ID; older versions need tree discovery.
      if (chrome.bookmarks.ROOT_NODE_ID !== undefined) {
        const root = await requireNode(chrome.bookmarks.ROOT_NODE_ID, true);
        roots = [{ ...root, children: await children(root) }];
      } else roots = await chrome.bookmarks.getTree();
      for (const root of roots) {
        if (rows.length + 1 + (root.children?.length ?? 0) > MAX_NODES) stop("bookmark_traversal_limit");
        rows.push({ node: root, depth: 0, omitted: false });
        for (const node of root.children ?? []) rows.push({ node, depth: 1 });
      }
    } else if (args.action === "children") rows = (await children(await requireNode(args.folder_id, true))).map((node) => ({ node, depth: 1 }));
    else if (args.action === "subtree") {
      const stack = [{ node: await requireNode(args.folder_id, true), depth: 0 }], seen = new Set();
      while (stack.length) {
        if (Date.now() >= expiresAt) stop("request_expired");
        const item = stack.pop();
        if (seen.has(item.node.id)) stop("bookmark_changed");
        seen.add(item.node.id); rows.push(item);
        if (rows.length + stack.length > MAX_NODES) stop("bookmark_traversal_limit");
        if (folder(item.node)) {
          if (item.depth >= (args.depth ?? 2)) { item.omitted = true; depthLimited = true; }
          else {
            const nested = await children(item.node);
            if (rows.length + stack.length + nested.length > MAX_NODES) stop("bookmark_traversal_limit");
            item.omitted = false;
            for (let index = nested.length - 1; index >= 0; index--) stack.push({ node: nested[index], depth: item.depth + 1 });
          }
        }
      }
    } else if (args.action === "search") {
      const found = await chrome.bookmarks.search(Object.fromEntries(["query", "title", "url"].filter((key) => args[key] !== undefined)
        .map((key) => [key, args[key]])));
      if (found.length > MAX_NODES) stop("bookmark_traversal_limit");
      rows = found.map((node) => ({ node, depth: 0 }));
    } else rows = (await chrome.bookmarks.getRecent(MAX_NODES + 1)).map((node) => ({ node, depth: 0 }));
    if (rows.length > MAX_NODES) stop("bookmark_traversal_limit");
    const scope = [args.action, args.bookmark_id ?? null, args.folder_id ?? null, args.query ?? null, args.title ?? null, args.url ?? null,
      args.action === "subtree" ? args.depth ?? 2 : null];
    const snapshot = await fingerprint([scope, rows.map(({ node, depth, omitted }) => [state(node), depth, omitted ?? folder(node)])]);
    if (args.snapshot !== undefined && snapshot !== args.snapshot) stop("bookmark_changed");
    const offset = args.offset ?? 0, limit = args.limit ?? 50;
    if (offset > rows.length) stop("invalid_arguments");
    const page = rows.slice(offset, offset + limit), next = offset + page.length;
    await gate(expiresAt);
    return shared.boundedResult({ action: args.action, nodes: page.map(({ node, depth, omitted }) => display(node, depth, omitted)),
      total: rows.length, offset, limit, has_more: next < rows.length, next_offset: next < rows.length ? next : null,
      snapshot, depth_limited: depthLimited });
  }
  function prunePreviews() {
    for (const [key, preview] of previews) if (Date.now() - preview.created >= 120000 || previews.size > 32) previews.delete(key);
  }
  async function tree(folderId, expiresAt) {
    const node = await requireNode(folderId, true);
    await writable(node);
    const roots = await chrome.bookmarks.getSubTree(folderId);
    if (roots.length !== 1 || !same(node, roots[0])) stop("bookmark_changed");
    const stack = [{ node: roots[0], depth: 0 }], rows = [], seen = new Set();
    let bytes = 0, bookmarks = 0, folders = 0;
    while (stack.length) {
      if (Date.now() >= expiresAt) stop("request_expired");
      const item = stack.pop(), current = item.node;
      if (seen.has(current.id)) stop("bookmark_changed");
      seen.add(current.id);
      if (item.depth > MAX_DEPTH || rows.length + stack.length >= MAX_NODES) stop("bookmark_traversal_limit");
      if (current.unmodifiable !== undefined || current.folderType !== undefined || current.parentId === undefined) stop("bookmark_protected");
      bytes += new TextEncoder().encode(JSON.stringify(state(current))).length + 32;
      if (bytes > MAX_BYTES) stop("bookmark_traversal_limit");
      rows.push(item);
      if (folder(current)) {
        if (!Array.isArray(current.children)) stop("bookmark_changed");
        if (item.depth) folders++;
        if (rows.length + stack.length + current.children.length > MAX_NODES) stop("bookmark_traversal_limit");
        for (let index = current.children.length - 1; index >= 0; index--) {
          const child = current.children[index];
          if (child.parentId !== current.id || child.index !== index) stop("bookmark_changed");
          stack.push({ node: child, depth: item.depth + 1 });
        }
      } else { if (current.children?.length) stop("bookmark_changed"); bookmarks++; }
    }
    return { node, rows, bookmarks, folders, snapshot: await fingerprint(rows.map(({ node }) => state(node))) };
  }
  async function preview(args, expiresAt) {
    const observed = await tree(args.folder_id, expiresAt), created = Date.now();
    const preview_id = `bmk_${crypto.randomUUID()}`;
    const result = shared.boundedResult({ folder_id: args.folder_id, folder: display(observed.node),
      bookmark_count: observed.bookmarks, subfolder_count: observed.folders, total_nodes: observed.rows.length,
      examples: observed.rows.slice(0, args.examples_limit ?? 10).map(({ node, depth }) => display(node, depth)),
      has_more: observed.rows.length > (args.examples_limit ?? 10), preview_id, expires_at: created + 120000 });
    if (!result.ok) return result;
    await gate(expiresAt);
    previews.set(preview_id, { folderId: args.folder_id, snapshot: observed.snapshot, created }); prunePreviews();
    return result;
  }
  async function execute(name, args, expiresAt) {
    let attempted = false;
    try {
      await gate(expiresAt);
      if (name === "browser.bookmarks.query") return await query(args, expiresAt);
      if (name === "browser.bookmarks.preview_remove_tree") return await preview(args, expiresAt);
      if (name === "browser.bookmarks.remove_tree") {
        prunePreviews();
        const credential = previews.get(args.preview_id);
        if (!credential || credential.folderId !== args.folder_id) stop("bookmark_preview_unavailable");
        const observed = await tree(args.folder_id, expiresAt);
        if (previews.get(args.preview_id) !== credential) stop("bookmark_preview_unavailable");
        if (observed.snapshot !== credential.snapshot) stop("bookmark_changed");
        const current = await requireNode(args.folder_id, true);
        if (!same(current, observed.node)) stop("bookmark_changed");
        await writable(current);
        await gate(expiresAt);
        if (previews.get(args.preview_id) !== credential || Date.now() - credential.created >= 120000) stop("bookmark_preview_unavailable");
        // Consume synchronously before dispatch, including API errors. This is
        // a snapshot credential, never technical proof of conversation consent.
        for (const [key, preview] of previews) if (preview.folderId === args.folder_id) previews.delete(key);
        attempted = true;
        await chrome.bookmarks.removeTree(args.folder_id);
        if (await get(args.folder_id)) stop("state_not_reached");
        await gate(expiresAt);
        return shared.boundedResult({ folder_id: args.folder_id, removed: true, total_nodes: observed.rows.length });
      }
      const node = args.action === "create" ? null : await requireNode(args.bookmark_id);
      if (node) await writable(node);
      if (args.action === "move" && folder(node)) await tree(node.id, expiresAt);
      if (args.action === "update" && folder(node) && args.url !== undefined) stop("bookmark_not_bookmark");
      let parent, siblingsBefore;
      if (["create", "move"].includes(args.action)) {
        parent = await requireNode(args.parent_id, true);
        await writable(parent, true, args.action === "move" && folder(node) ? node.id : undefined);
        if (args.action === "move") siblingsBefore = await children(parent);
      }
      if (args.action === "remove" && folder(node) && (await children(node)).length) stop("bookmark_not_empty");
      if (node && !same(node, await requireNode(node.id))) stop("bookmark_changed");
      if (parent && !same(parent, await requireNode(parent.id, true))) stop("bookmark_changed");
      if (args.action === "remove" && folder(node) && (await children(node)).length) stop("bookmark_not_empty");
      await gate(expiresAt);
      attempted = true;
      if (args.action === "remove") {
        await chrome.bookmarks.remove(node.id);
        if (await get(node.id)) stop("state_not_reached");
        await gate(expiresAt);
        return shared.boundedResult({ action: args.action, node: display(node), removed: true });
      }
      const returned = args.action === "create" ? await chrome.bookmarks.create({ parentId: args.parent_id, title: args.title,
        ...(args.index === undefined ? {} : { index: args.index }), ...(args.url === undefined ? {} : { url: args.url }) })
        : args.action === "update" ? await chrome.bookmarks.update(node.id, Object.fromEntries(["title", "url"]
          .filter((key) => args[key] !== undefined).map((key) => [key, args[key]])))
          : await chrome.bookmarks.move(node.id, { parentId: args.parent_id, ...(args.index === undefined ? {} : { index: args.index }) });
      if (!returned || !id(returned.id) || node && returned.id !== node.id) stop("state_not_reached");
      const actual = await get(returned.id);
      if (!actual || !same(actual, returned) || !Number.isInteger(actual.index) || actual.index < 0
        || (args.action === "create" ? folder(actual) !== (args.kind === "folder") : folder(actual) !== folder(node))
        || args.title !== undefined && actual.title !== args.title
        || args.url !== undefined && actual.url !== canonicalURL(args.url)
        || parent && actual.parentId !== parent.id) stop("state_not_reached");
      if (args.action === "move") {
        if (actual.title !== node.title || actual.url !== node.url) stop("state_not_reached");
        const siblings = await children(await requireNode(parent.id, true));
        if (siblings[actual.index]?.id !== actual.id) stop("state_not_reached");
        const othersBefore = siblingsBefore.filter((item) => item.id !== node.id), others = siblings.filter((item) => item.id !== node.id);
        if (JSON.stringify(others.map((item) => item.id)) !== JSON.stringify(othersBefore.map((item) => item.id))) stop("state_not_reached");
        // Insertion indices refer to the pre-removal list. Same-parent forward
        // moves shift by one; an API that clamps oversized indices may use end.
        let expected = Math.min(args.index ?? siblingsBefore.length, siblingsBefore.length);
        if (node.parentId === parent.id && node.index < expected) expected--;
        if (actual.index !== expected) stop("state_not_reached");
      }
      await gate(expiresAt);
      return shared.boundedResult({ action: args.action, node: display(actual), removed: false });
    } catch (error) {
      let code = error instanceof Failure ? error.code : "browser_operation_failed";
      // Another request can consume the credential while native reads yield.
      // Report that invalidation, even if its delete made a later read fail.
      if (!attempted && name === "browser.bookmarks.remove_tree" && !previews.has(args.preview_id)
        && !["bookmarks_permission_denied", "request_expired"].includes(code)) code = "bookmark_preview_unavailable";
      return shared.fail(attempted && !["request_expired", "bookmarks_permission_denied", "state_not_reached"].includes(code)
        ? "bookmark_outcome_unknown" : code);
    }
  }
  globalThis.Chat2LocalBookmarks = { configure: (helpers) => { shared = helpers; }, valid, execute };
})();
