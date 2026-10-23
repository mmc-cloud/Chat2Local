/* Shared output filtering. Never truncate a credential before redacting it. */
(() => {
  const normalize = (value) => String(value ?? "").replace(/\s+/gu, " ").trim();
  const secret = /password|passwd|token|secret|cookie|authorization|api[-_]?key|csrf|session[-_]?id|one[-_]?time|cc-number/i;
  const redact = (value) => value
    .replace(/\b(?:Bearer\s+\S*|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]*|[A-Za-z0-9_-]{32,})\b/giu, "[redacted]")
    .replace(/\b(?:password|passwd|(?:access|refresh|csrf)[-_]?token|token|secret|cookie|authorization|api[-_]?key|session[-_]?id)\s*[:=]\s*(?:"[^"]*(?:"|$)|'[^']*(?:'|$)|\S*)/giu, "[redacted]");
  function url(value, base) {
    if (String(value).length > 8192) return { url: null, truncated: true };
    try {
      const parsed = new URL(value, base);
      if (!["http:", "https:"].includes(parsed.protocol)) return { url: null, truncated: false };
      parsed.username = ""; parsed.password = ""; parsed.search = ""; parsed.hash = "";
      let hideNext = false;
      parsed.pathname = parsed.pathname.split("/").map((part) => {
        let decoded;
        try { decoded = decodeURIComponent(part); } catch { return "[redacted]"; }
        const sensitive = secret.test(decoded), hide = hideNext || sensitive || redact(decoded) !== decoded;
        hideNext = sensitive;
        return hide ? "[redacted]" : part;
      }).join("/");
      return { url: parsed.href.length > 2048 ? parsed.origin : parsed.href, truncated: parsed.href.length > 2048 };
    } catch { return { url: null, truncated: false }; }
  }
  function content(value, collapse = true) {
    const raw = String(value ?? "");
    const filtered = redact(raw.replace(/https?:\/\/[^\s<>"']+/giu, (value) => url(value).url || "[redacted]"));
    return collapse ? normalize(filtered) : filtered;
  }
  function text(value, max = 160) {
    const raw = String(value ?? "");
    // Oversized attributes are omitted as a whole; a prefix is not safe evidence.
    if (raw.length > 65536) return { text: "[redacted]", truncated: true };
    const filtered = content(raw);
    return { text: Array.from(filtered).slice(0, max).join(""), truncated: Array.from(filtered).length > max };
  }
  globalThis.Chat2LocalSafety = { text, url, content };
})();
