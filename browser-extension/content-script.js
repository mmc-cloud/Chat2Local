/* Static, isolated-world DOM operations. Top document only; no page JS eval. */
(() => {
  if (globalThis.__chat2localDOMInstalled) return;
  globalThis.__chat2localDOMInstalled = true;
  const fail = (code, details) => ({ ok: false, error: { code, ...(details ? { details } : {}) } });
  const normalize = (value) => String(value ?? "").replace(/\s+/gu, " ").trim();
  const clip = (value, max = 160) => normalize(value).slice(0, max);
  const controls = 'button,a[href],input,textarea,select,summary,[role],[contenteditable],[aria-label],[aria-labelledby],[placeholder],[id]';
  const semanticControls = 'button,a[href],input,textarea,select,summary,label,[role="button"],[role="link"],[role="textbox"],'
    + '[role="checkbox"],[role="radio"],[role="combobox"],[role="switch"],[role="menuitem"],[role="tab"],[role="option"]';
  const protectedTags = 'input,textarea,select,option,script,style,template,noscript,iframe,object,embed,[contenteditable]:not([contenteditable="false"])';
  const secretHint = /password|passwd|token|secret|cookie|authorization|api[-_]?key|csrf|session[-_]?id|one[-_]?time|cc-number/i;
  class QueryError extends Error { constructor(code) { super(code); this.code = code; } }
  // Document-local weak references: any DOM mutation invalidates all issued refs.
  // No node attributes are added and no page content or strong node cache is kept.
  const refDocument = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
  let refSequence = 0, referenceUrl = location.href, references = new Map(), nodeReferences = new WeakMap();
  const invalidateReferences = () => { references.clear(); nodeReferences = new WeakMap(); };
  const mutations = new MutationObserver(invalidateReferences);
  mutations.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  function syncReferences() {
    if (mutations.takeRecords().length || location.href !== referenceUrl) invalidateReferences();
    referenceUrl = location.href;
  }
  function reference(element) {
    syncReferences();
    let ref = nodeReferences.get(element);
    if (!ref || !references.has(ref)) {
      ref = `el_${refDocument}_${++refSequence}`;
      references.set(ref, { node: new WeakRef(element), url: location.href });
      nodeReferences.set(element, ref);
      if (references.size > 10000) references.delete(references.keys().next().value);
    }
    return ref;
  }
  function referenced(ref) {
    syncReferences();
    const record = references.get(ref), element = record?.node.deref();
    if (!element?.isConnected || record.url !== location.href) throw new QueryError("stale_target");
    return element;
  }
  function query(root, css) {
    try {
      const found = root.querySelectorAll(css);
      if (found.length > 10000) throw new QueryError("page_too_large");
      return [...found];
    } catch (error) {
      if (error.name === "SyntaxError") throw new QueryError("invalid_selector");
      throw error;
    }
  }
  const protectedNode = (element) => element.matches(protectedTags)
    || secretHint.test(`${element.id} ${element.getAttribute("name") || ""} ${element.getAttribute("autocomplete") || ""}`);
  function protectedContent(element) {
    for (let node = element; node; node = node.parentElement) if (protectedNode(node)) return true;
    return false;
  }
  const textBudget = (expiresAt = Infinity) => ({ reads: 40000, strict: true, expiresAt });
  function boundedText(element, budget = textBudget()) {
    if (protectedContent(element)) return { text: "", truncated: false };
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => node.nodeType === Node.ELEMENT_NODE && (protectedNode(node) || !visible(node))
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    let raw = "", count = 0, truncated = false, node;
    while ((node = walker.nextNode())) {
      if (Date.now() >= budget.expiresAt) throw new QueryError("request_expired");
      if (--budget.reads < 0) {
        if (budget.strict) throw new QueryError("page_too_large");
        truncated = true; break;
      }
      if (++count > 200 || raw.length >= 2048) { truncated = true; break; }
      if (node.nodeType === Node.TEXT_NODE) {
        const remaining = 2048 - raw.length;
        raw += node.data.slice(0, remaining);
        if (node.data.length > remaining) { truncated = true; break; }
      }
    }
    return { text: normalize(raw), truncated };
  }
  function exactText(info, expected) {
    const wanted = normalize(expected);
    // An incomplete prefix may still be a full match (e.g. trailing whitespace).
    // Refuse an uncertain candidate rather than silently proving uniqueness.
    if (info.truncated && wanted.startsWith(info.text)) throw new QueryError("page_too_large");
    return !info.truncated && info.text === wanted;
  }
  function joinedText(elements, budget) {
    const parts = [];
    let truncated = false;
    for (const element of elements) {
      const info = boundedText(element, budget);
      parts.push(info.text);
      if (info.truncated) { truncated = true; break; }
    }
    return { text: normalize(parts.join(" ")), truncated };
  }
  // Preserve semantic controls inside each text branch, otherwise use the
  // deepest matching element. Ancestors with incidental aggregate text lose.
  function textLeaves(elements, root) {
    const parents = new Set(), matching = new Set(elements), owners = new Map();
    const stop = root.parentElement;
    for (const element of elements) {
      for (let node = element.parentElement; node && node !== stop && !parents.has(node); node = node.parentElement) parents.add(node);
    }
    return elements.filter((element) => {
      if (element.matches(semanticControls)) return true;
      if (parents.has(element)) return false;
      let node = element.parentElement, owner = null;
      const trail = [];
      while (node && node !== stop) {
        if (owners.has(node)) { owner = owners.get(node); break; }
        trail.push(node);
        if (matching.has(node) && node.matches(semanticControls)) { owner = node; break; }
        node = node.parentElement;
      }
      for (const parent of trail) owners.set(parent, owner);
      return !owner;
    });
  }
  function textSearch(root, target, expiresAt, tag) {
    if (protectedContent(root)) return [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
      acceptNode: (node) => protectedNode(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const elements = [], budget = textBudget(expiresAt);
    let node, count = 0;
    while ((node = walker.nextNode())) {
      if (++count > 10000) throw new QueryError("page_too_large");
      if (Date.now() >= expiresAt) throw new QueryError("request_expired");
      if (!(node instanceof HTMLElement) || node.matches("html,body,head")) continue;
      if (tag && node.localName !== tag.toLowerCase()) continue;
      if (matches(node, target, budget)) elements.push(node);
    }
    return textLeaves(elements, root);
  }
  function role(element) {
    const explicit = normalize(element.getAttribute("role")).split(" ")[0];
    if (explicit) return explicit;
    const tag = element.localName, type = element.type;
    if (tag === "button" || tag === "summary" || (tag === "input" && ["button", "submit", "reset", "image"].includes(type))) return "button";
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "textarea" || (tag === "input" && ["text", "search", "email", "tel", "url", "password"].includes(type))) return "textbox";
    if (tag === "input" && ["checkbox", "radio"].includes(type)) return type;
    if (tag === "select") return element.multiple ? "listbox" : "combobox";
    return "";
  }
  function name(element, budget = textBudget()) {
    const ids = normalize(element.getAttribute("aria-labelledby"));
    if (ids) {
      const info = joinedText(ids.split(" ").map((id) => document.getElementById(id)).filter(Boolean), budget);
      if (info.text || info.truncated) return info;
    }
    if (element.hasAttribute("aria-label")) return { text: normalize(element.getAttribute("aria-label")), truncated: false };
    if (element.labels?.length) {
      const info = joinedText(element.labels, budget);
      if (info.text || info.truncated) return info;
    }
    if (element.getAttribute("alt")) return { text: normalize(element.getAttribute("alt")), truncated: false };
    const info = boundedText(element, budget);
    if (info.text || info.truncated) return info;
    return { text: normalize(element.getAttribute("title") || element.getAttribute("placeholder")), truncated: false };
  }
  function styleVisible(element) {
    const style = getComputedStyle(element);
    return !element.hidden && element.getAttribute("aria-hidden") !== "true" && style.display !== "none"
      && !["hidden", "collapse"].includes(style.visibility) && style.opacity !== "0" && style.contentVisibility !== "hidden";
  }
  function visible(element) {
    for (let node = element; node instanceof Element; node = node.parentElement) {
      if (!styleVisible(node)) return false;
    }
    return element.getClientRects().length > 0;
  }
  const disabled = (element) => element.matches(":disabled") || Boolean(element.closest('[aria-disabled="true"],[inert],.disable,.disabled,.is-disabled'));
  function matches(element, target, budget = textBudget()) {
    const values = { role: () => role(element), aria_label: () => normalize(element.getAttribute("aria-label")),
      placeholder: () => normalize(element.getAttribute("placeholder")), href: () => element.href || "",
      id: () => element.id };
    return Object.entries(target || {}).every(([key, value]) => key === "ref" ? referenced(value) === element : key === "css" ? element.matches(value)
      : key === "name" ? exactText(name(element, budget), value)
      : key === "text" ? exactText(boundedText(element, budget), value)
      : key === "label" ? [...(element.labels || [])].some((label) => exactText(boundedText(label, budget), value))
      : values[key]?.() === (["href", "id", "role"].includes(key) ? value : normalize(value)));
  }
  function candidates(target, expiresAt = Infinity) {
    if (target?.ref) {
      const element = referenced(target.ref);
      return matches(element, target, textBudget(expiresAt)) ? [element] : [];
    }
    if (target?.text && !target.css) return textSearch(document.documentElement, target, expiresAt);
    const elements = query(document, target?.css || controls);
    const budget = textBudget(expiresAt);
    const matched = elements.filter((element) => matches(element, target, budget));
    if (target?.name && !target.css && !target.id && (!matched.length || matched.some((element) =>
      !element.matches(semanticControls) && !element.hasAttribute("aria-label") && !element.hasAttribute("aria-labelledby") && !element.labels?.length))) {
      const semantic = matched.filter((element) => element.matches(semanticControls) || element.hasAttribute("aria-label")
        || element.hasAttribute("aria-labelledby") || element.labels?.length);
      return textLeaves([...new Set([...semantic, ...textSearch(document.documentElement, target, expiresAt)])], document.documentElement);
    }
    return matched;
  }
  function domCandidates(args, expiresAt) {
    let root = document;
    if (args.scope) {
      const scopes = query(document, args.scope);
      if (!scopes.length) throw new QueryError("scope_not_found");
      if (scopes.length !== 1) throw new QueryError("ambiguous_scope");
      root = scopes[0];
    }
    let elements;
    if (args.css) elements = query(root, args.css);
    else if (args.text) elements = textSearch(root === document ? document.documentElement : root, { text: args.text }, expiresAt, args.tag);
    else elements = query(root, args.tag);
    const budget = textBudget(expiresAt);
    return { root, elements: elements.filter((element) => {
      if (!(element instanceof HTMLElement) || (args.tag && element.localName !== args.tag.toLowerCase())) return false;
      if (args.text === undefined) return true;
      const text = boundedText(element, budget);
      return exactText(text, args.text);
    }) };
  }
  const redactedText = (value, max = 160) => Chat2LocalSafety.text(value, max);
  function cssPath(element) {
    const parts = [];
    let work = 0;
    for (let node = element; node; node = node.parentElement) {
      if (!(node instanceof HTMLElement)) return null;
      let index = 1;
      for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
        if (++work > 10000) return null;
        index++;
      }
      parts.unshift(node.parentElement ? `*:nth-child(${index})` : ":root");
      if (parts.join(" > ").length > 1024) return null;
    }
    return parts.join(" > ");
  }
  function domSnapshot(elements, root, args, expiresAt) {
    const budget = { nodes: 40, bytes: 32 * 1024, reads: 8000, expiresAt, truncated: false };
    const allowed = ["id", "class", "role", "type", "name", "aria-label", "aria-disabled", "aria-hidden", "aria-expanded",
      "aria-checked", "aria-selected", "placeholder", "disabled", "readonly", "tabindex", "for"];
    function describe(element, depth) {
      if (Date.now() >= expiresAt) throw new QueryError("request_expired");
      if (budget.nodes <= 0 || budget.bytes < 2048) { budget.truncated = true; return null; }
      const sensitive = protectedContent(element), isVisible = visible(element);
      const text = sensitive || !isVisible ? { text: "", truncated: false } : boundedText(element, budget);
      let clipped = text.truncated;
      const present = (value, max = 160) => {
        const info = redactedText(value, max);
        if (info.truncated) clipped = true;
        return info.text;
      };
      const attributes = {};
      if (!sensitive) for (const key of allowed) {
        if (!element.hasAttribute(key)) continue;
        if (Object.keys(attributes).length >= 6) { clipped = true; break; }
        attributes[key] = present(element.getAttribute(key), 80);
      }
      const item = { tag: present(element.localName, 64), id: present(element.id), class: present(element.getAttribute("class")),
        role: present(role(element), 64), text: text.truncated ? "" : present(text.text),
        visible: isVisible, disabled: disabled(element), attributes, children: [], truncated: clipped };
      budget.nodes--;
      budget.bytes -= new TextEncoder().encode(JSON.stringify(item)).length + 64;
      if (depth > 0 && !sensitive) {
        let count = 0;
        for (const child of element.children) {
          if (++count > 5) { item.truncated = true; break; }
          const info = describe(child, depth - 1);
          if (!info) { item.truncated = true; break; }
          item.children.push(info);
          if (info.truncated) item.truncated = true;
        }
      }
      if (budget.bytes < 0) item.truncated = true;
      if (item.truncated) budget.truncated = true;
      return item;
    }
    const result = [];
    for (const element of elements.slice(0, args.limit ?? 5)) {
      const info = describe(element, args.depth ?? 1);
      if (!info) break;
      info.css = cssPath(element);
      info.ref = reference(element);
      budget.bytes -= (info.css?.length || 0) + 32;
      info.parents = [];
      for (let parent = element.parentElement; parent && info.parents.length < 2 && root.contains(parent); parent = parent.parentElement) {
        const ancestor = describe(parent, 0);
        if (!ancestor) { info.truncated = true; break; }
        info.parents.push(ancestor);
        if (ancestor.truncated) info.truncated = true;
        if (parent === root) break;
      }
      if (budget.bytes < 0) info.truncated = true;
      if (info.truncated) budget.truncated = true;
      result.push(info);
    }
    return { ok: true, result: { elements: result, total: elements.length,
      truncated: budget.truncated || result.length < elements.length } };
  }
  function summary(element) {
    let truncated = false;
    const present = (value, max = 160) => { const info = redactedText(value, max); truncated ||= info.truncated; return info.text; };
    const info = name(element), href = readUrl(element.href || "");
    truncated ||= info.truncated || href.truncated;
    return { ref: reference(element), role: present(role(element), 64), name: info.truncated ? "" : present(info.text), id: present(element.id),
      href: element.href ? href.url : "", aria_label: present(element.getAttribute("aria-label")),
      placeholder: present(element.getAttribute("placeholder")), visible: visible(element),
      disabled: disabled(element), read_only: Boolean(element.readOnly), input_type: present(element.type || "", 32), truncated };
  }
  function readUrl(value) {
    return Chat2LocalSafety.url(value, document.baseURI);
  }
  function readContent(value, collapse = true) {
    return Chat2LocalSafety.content(value, collapse);
  }
  function readField(value, max) {
    return redactedText(value, max);
  }
  function readPage(expiresAt) {
    const blocks = [], overview = { headings: 0, links: 0, controls: 0, images: 0 };
    const names = textBudget(expiresAt);
    let visited = 0, rawChars = 0, blockId = 0, pending = null, truncated = false;
    const url = readUrl(location.href), title = readField(document.title, 512);
    const description = readField(document.querySelector('meta[name="description"]')?.getAttribute("content"), 512);
    const language = readField(document.documentElement?.getAttribute("lang"), 64);
    truncated = url.truncated || title.truncated || description.truncated || language.truncated;
    const page = { url: url.url, title: title.text, description: description.text, language: language.text, truncated };
    const boundaries = new Set(["div", "section", "article", "main", "nav", "aside", "header", "footer", "p", "li",
      "ul", "ol", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "blockquote", "pre", "dt", "dd",
      "h1", "h2", "h3", "h4", "h5", "h6"]);
    const structure = (context) => Object.fromEntries(["ref", "heading_level", "list_depth", "ordered", "row", "column", "header"]
      .filter((key) => context[key] !== undefined).map((key) => [key, context[key]]));
    function append(context, raw, force = false) {
      const text = readContent(raw, context.type !== "code");
      if (!text && !force) return;
      const id = blockId++;
      const characters = Array.from(text);
      const parts = Math.max(1, Math.ceil(characters.length / 1000));
      for (let part = 0; part < parts; part++) {
        if (blocks.length >= 10000) throw new QueryError("page_too_large");
        blocks.push({ ...context, block_id: id, part, text: characters.slice(part * 1000, (part + 1) * 1000).join("") });
      }
      if (context.truncated) truncated = true;
    }
    function flush() {
      if (pending) append(pending.context, pending.raw);
      pending = null;
    }
    function visit(node, context, depth, listDepth, ordered) {
      if (Date.now() >= expiresAt) throw new QueryError("request_expired");
      if (++visited > 50000 || depth > 128) throw new QueryError("page_too_large");
      if (node.nodeType === Node.TEXT_NODE) {
        rawChars += node.data.length;
        if (rawChars > 1000000) throw new QueryError("page_too_large");
        if (!pending || pending.context !== context) { flush(); pending = { context, raw: "" }; }
        pending.raw += node.data;
        return;
      }
      if (!(node instanceof HTMLElement) || !styleVisible(node)) return;
      const tag = node.localName;
      if (tag.length > 64) throw new QueryError("page_too_large");
      if (tag === "details" && !node.open) {
        flush();
        const summary = [...node.children].find((child) => child.localName === "summary");
        if (summary) visit(summary, context, depth + 1, listDepth, ordered);
        return;
      }
      const control = node.matches(semanticControls) || node.hasAttribute("tabindex");
      // Read only explicit control metadata from protected form fields, never values.
      if (protectedNode(node)) {
        if (control && ["input", "textarea", "select"].includes(tag)) {
          flush(); overview.controls++;
          const info = name(node, names), label = readField(info.text, 256), roleInfo = readField(role(node), 64);
          append({ ...structure(context), type: "control", tag, depth, ref: reference(node), role: roleInfo.text, name: info.truncated ? "" : label.text, disabled: disabled(node),
            truncated: info.truncated || label.truncated || roleInfo.truncated }, "", true);
        }
        return;
      }
      if (tag === "br") { if (pending) pending.raw += "\n"; return; }
      if (tag === "img") {
        flush();
        const alt = readField(node.getAttribute("alt") || node.getAttribute("aria-label") || node.getAttribute("title"), 512);
        if (alt.text) {
          overview.images++;
          append({ ...structure(context), type: "image", tag, depth, disabled: disabled(node), truncated: alt.truncated,
            ...(context.type === "link" ? { url: context.url, ref: context.ref } : {}) }, alt.text);
        }
        return;
      }
      const boundary = boundaries.has(tag) || tag === "a" || control;
      if (boundary) flush();
      let next = context;
      if (boundary) {
        next = { ...structure(context), type: "text", tag, depth, disabled: disabled(node) };
        if (/^h[1-6]$/.test(tag)) {
          next.type = "heading"; next.level = Number(tag[1]); next.heading_level = next.level; overview.headings++;
        }
        else if (tag === "p" || tag === "dt" || tag === "dd") next.type = "paragraph";
        else if (tag === "pre") next.type = "code";
        else if (tag === "blockquote") next.type = "quote";
        else if (tag === "li") Object.assign(next, { type: "list_item", list_depth: listDepth, ordered });
        else if (tag === "td" || tag === "th") Object.assign(next, { type: "table_cell", row: node.parentElement.rowIndex + 1,
          column: node.cellIndex + 1, header: tag === "th" });
        if (tag === "a" && node.hasAttribute("href")) {
          const link = readUrl(node.getAttribute("href"));
          Object.assign(next, { type: "link", ref: reference(node), url: link.url, truncated: link.truncated }); overview.links++;
        } else if (control && tag !== "label") {
          const info = name(node, names), label = readField(info.text, 256), roleInfo = readField(role(node), 64);
          Object.assign(next, { type: "control", ref: reference(node), role: roleInfo.text, name: info.truncated ? "" : label.text,
            truncated: info.truncated || label.truncated || roleInfo.truncated }); overview.controls++;
        }
      }
      const before = blocks.length;
      for (const child of node.childNodes) visit(child, next, depth + 1,
        tag === "ul" || tag === "ol" ? listDepth + 1 : listDepth, tag === "ul" || tag === "ol" ? tag === "ol" : ordered);
      if (boundary) {
        flush();
        if (blocks.length === before && (next.type === "control" || next.type === "link")) append(next, "", true);
      }
    }
    const body = document.body;
    if (body && !protectedContent(body) && styleVisible(document.documentElement)) {
      visit(body, { type: "text", tag: "body", depth: 0, disabled: disabled(body) }, 0, 0, false);
      flush();
    }
    return { ok: true, result: { page, overview, blocks, truncated } };
  }
  const validDocument = (message) => location.href === message.expected_url;
  async function perform(message) {
    const args = message.arguments;
    if (!args || !["read", "inspect", "dom.inspect", "click", "fill"].includes(message.operation)) return fail("invalid_arguments");
    const reading = message.operation === "read", domInspect = message.operation === "dom.inspect";
    const inspect = reading || domInspect || message.operation === "inspect";
    const find = () => reading ? [] : domInspect ? domCandidates(args, message.expires_at).elements : candidates(args.target, message.expires_at);
    const deadline = Math.min(message.expires_at - 100, Date.now() + (args.wait_ms || 0));
    let elements;
    do {
      if (!validDocument(message)) return fail("page_changed");
      if (Date.now() >= message.expires_at) return fail("request_expired");
      elements = find();
      if (elements === null) return fail("page_too_large");
      if (elements.length || Date.now() >= deadline) break;
      await new Promise((resolve) => setTimeout(resolve, Math.min(50, deadline - Date.now())));
    } while (true);
    if (!elements.length && (args.target || domInspect || !inspect)) return fail("target_not_found");
    if (!inspect) {
      elements = elements.filter(visible);
      if (!elements.length) return fail("target_hidden");
    }
    // Recheck permission after waiting; revocation must not leave a usable listener.
    const authorization = await chrome.runtime.sendMessage({ type: "chat2local.dom.authorize",
      expected_url: message.expected_url, tab: args.tab });
    if (authorization?.ok !== true) return fail(authorization?.error?.code === "page_changed" ? "page_changed" : "permission_denied");
    if (!validDocument(message)) return fail("page_changed");
    if (Date.now() >= message.expires_at) return fail("request_expired");
    if (!inspect && elements.length !== 1) return fail("ambiguous_target", { candidates: elements.slice(0, 5).map(summary) });
    if (inspect) {
      if (reading) {
        const result = readPage(message.expires_at);
        if (!validDocument(message)) return fail("page_changed");
        if (Date.now() >= message.expires_at) return fail("request_expired");
        return result;
      }
      if (domInspect) {
        const found = domCandidates(args, message.expires_at);
        if (!found.elements.length) return fail("target_not_found");
        const snapshot = domSnapshot(found.elements, found.root, args, message.expires_at);
        if (!validDocument(message)) return fail("page_changed");
        if (Date.now() >= message.expires_at) return fail("request_expired");
        return snapshot;
      }
      elements = find();
      if (elements === null) return fail("page_too_large");
      if (args.target && !elements.length) return fail("target_not_found");
      const summaries = elements.slice(0, args.limit ?? 20).map(summary);
      return { ok: true, result: { elements: summaries,
        total: elements.length, truncated: elements.length > (args.limit ?? 20) || summaries.some((item) => item.truncated) } };
    }
    const element = elements[0];
    const check = () => {
      if (!validDocument(message)) return fail("page_changed");
      if (Date.now() >= message.expires_at) return fail("request_expired");
      const current = candidates(args.target, message.expires_at);
      if (current === null) return fail("page_too_large");
      if (!element.isConnected || !current.includes(element)) return fail("stale_target");
      if (!visible(element)) return fail("target_hidden");
      const visibleMatches = current.filter(visible);
      if (visibleMatches.length !== 1) return fail("ambiguous_target", { candidates: visibleMatches.slice(0, 5).map(summary) });
      if (disabled(element)) return fail("target_disabled");
      if (message.operation === "click" && !(element instanceof HTMLElement)) return fail("unsupported_element");
      if (message.operation === "fill") {
        if (element.localName === "input" && element.type === "password") return fail("protected_input");
        if (element.readOnly || element.getAttribute("aria-readonly") === "true") return fail("target_readonly");
        if (!(element instanceof HTMLTextAreaElement) && !(element instanceof HTMLInputElement
          && ["text", "search", "email", "url", "tel"].includes(element.type))) return fail("unsupported_element");
      }
      return null;
    };
    let error = check();
    if (error) return error;
    element.scrollIntoView?.({ block: "center", inline: "nearest" });
    error = check();
    if (error) return error;
    element.focus(); // Does not add tabindex or change custom controls' focusability.
    error = check();
    if (error) return error;
    const target = summary(element);
    if (message.operation === "click") {
      element.click();
      return { ok: true, result: { status: "clicked", target, event_is_trusted: false, navigation_waited: false } };
    }
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, args.value);
    element.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    element.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return { ok: true, result: { status: "filled", target, submitted: false } };
  }
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || message?.type !== "chat2local.dom") return false;
    if (window !== window.top) { sendResponse(fail("unsupported_page")); return false; }
    void perform(message).then(sendResponse, (error) => sendResponse(fail(error instanceof QueryError ? error.code : "browser_operation_failed")));
    return true;
  });
})();
