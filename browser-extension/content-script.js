/* Static, isolated-world DOM operations. Top document only; no page JS eval. */
(() => {
  if (globalThis.__chat2localDOMInstalled) return;
  globalThis.__chat2localDOMInstalled = true;
  const fail = (code, details) => ({ ok: false, error: { code, ...(details ? { details } : {}) } });
  const normalize = (value) => String(value ?? "").replace(/\s+/gu, " ").trim();
  const clip = (value, max = 160) => normalize(value).slice(0, max);
  const controls = 'button,a[href],input,textarea,select,summary,[role],[contenteditable],[aria-label],[aria-labelledby],[placeholder],[id]';
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
  const labels = (element) => [...(element.labels || [])].map((label) => normalize(label.textContent));
  function name(element) {
    const ids = normalize(element.getAttribute("aria-labelledby"));
    if (ids) {
      const text = ids.split(" ").map((id) => document.getElementById(id)?.textContent || "").join(" ");
      if (normalize(text)) return normalize(text);
    }
    if (element.hasAttribute("aria-label")) return normalize(element.getAttribute("aria-label"));
    if (labels(element).length) return labels(element).join(" ");
    if (element.localName === "input" && ["button", "submit", "reset"].includes(element.type)) return normalize(element.value);
    return normalize(element.getAttribute("alt") || (element.localName !== "input" && element.textContent)
      || element.getAttribute("title") || element.getAttribute("placeholder"));
  }
  function visible(element) {
    for (let node = element; node instanceof Element; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (node.hidden || node.getAttribute("aria-hidden") === "true" || style.display === "none"
        || ["hidden", "collapse"].includes(style.visibility) || style.opacity === "0") return false;
    }
    return element.getClientRects().length > 0;
  }
  const disabled = (element) => element.matches(":disabled") || Boolean(element.closest('[aria-disabled="true"],[inert]'));
  function matches(element, target) {
    const values = { role: role(element), name: name(element), aria_label: normalize(element.getAttribute("aria-label")),
      label: labels(element), placeholder: normalize(element.getAttribute("placeholder")),
      href: element.href || "", text: normalize(element.textContent), id: element.id };
    return Object.entries(target || {}).every(([key, value]) => key === "label" ? values.label.includes(normalize(value))
      : values[key] === (["href", "id", "role"].includes(key) ? value : normalize(value)));
  }
  function candidates(target) {
    const elements = [...document.querySelectorAll(controls)];
    if (elements.length > 10000) return null; // Never silently miss duplicate targets beyond a scan cap.
    return elements.filter((element) => matches(element, target));
  }
  function summary(element) {
    return { role: clip(role(element), 64), name: clip(name(element)), id: clip(element.id),
      href: (element.href || "").slice(0, 2048), aria_label: clip(element.getAttribute("aria-label")),
      placeholder: clip(element.getAttribute("placeholder")), visible: visible(element),
      disabled: disabled(element), read_only: Boolean(element.readOnly), input_type: clip(element.type || "", 32) };
  }
  const validDocument = (message) => location.href === message.expected_url;
  async function perform(message) {
    const args = message.arguments;
    if (!args || !["inspect", "click", "fill"].includes(message.operation)) return fail("invalid_arguments");
    const deadline = Math.min(message.expires_at - 100, Date.now() + (args.wait_ms || 0));
    let elements;
    do {
      if (!validDocument(message)) return fail("page_changed");
      if (Date.now() >= message.expires_at) return fail("request_expired");
      elements = candidates(args.target);
      if (elements === null) return fail("page_too_large");
      if (elements.length || Date.now() >= deadline) break;
      await new Promise((resolve) => setTimeout(resolve, Math.min(50, deadline - Date.now())));
    } while (true);
    if (!elements.length && (args.target || message.operation !== "inspect")) return fail("target_not_found");
    if (message.operation !== "inspect" && elements.length !== 1) {
      return fail("ambiguous_target", { candidates: elements.slice(0, 5).map(summary) });
    }
    // Recheck permission after waiting; revocation must not leave a usable listener.
    const authorization = await chrome.runtime.sendMessage({ type: "chat2local.dom.authorize",
      expected_url: message.expected_url, tab: args.tab });
    if (authorization?.ok !== true) return fail(authorization?.error?.code === "page_changed" ? "page_changed" : "permission_denied");
    if (!validDocument(message)) return fail("page_changed");
    if (Date.now() >= message.expires_at) return fail("request_expired");
    if (message.operation === "inspect") {
      elements = candidates(args.target);
      if (elements === null) return fail("page_too_large");
      if (args.target && !elements.length) return fail("target_not_found");
      return { ok: true, result: { elements: elements.slice(0, args.limit ?? 20).map(summary),
        total: elements.length, truncated: elements.length > (args.limit ?? 20) } };
    }
    const element = elements[0];
    const check = () => {
      if (!validDocument(message)) return fail("page_changed");
      if (Date.now() >= message.expires_at) return fail("request_expired");
      const current = candidates(args.target);
      if (!element.isConnected || current?.length !== 1 || current[0] !== element) return fail("stale_target");
      if (!visible(element)) return fail("target_hidden");
      if (disabled(element)) return fail("target_disabled");
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
    if (message.operation === "click" && !element.matches('button,a[href],input,select,summary,[role="button"],[role="link"],[tabindex]')) {
      return fail("unsupported_element");
    }
    element.scrollIntoView?.({ block: "center", inline: "nearest" });
    element.focus(); // Focus handlers can replace nodes in a controlled UI.
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
    void perform(message).then(sendResponse, () => sendResponse(fail("browser_operation_failed")));
    return true;
  });
})();
