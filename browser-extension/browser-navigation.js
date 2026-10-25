/* Read-only observations and bounded waits. No document scripts or navigation retries. */
(() => {
  let shared;
  const records = new Map(), latest = new Map();
  // Random capabilities into a bounded, worker-local URL store. Never encode URLs
  // in identifiers or reuse display-only sanitized URLs as comparison baselines.
  const baselines = new Map();
  const baselinePattern = /^url_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  function pruneBaselines() {
    for (const [id, record] of baselines) {
      if (Date.now() - record.created >= 120000 || baselines.size > 128) baselines.delete(id);
    }
  }
  function baseline(tab) {
    if (!tab || typeof tab.url !== "string" || !tab.url) return null;
    const id = `url_${crypto.randomUUID()}`;
    baselines.set(id, { tabId: tab.id, url: tab.url, created: Date.now() });
    pruneBaselines();
    return id;
  }
  const idPattern = /^nav_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  function prune() {
    for (const [id, record] of records) {
      if (Date.now() - record.created > 120000 || records.size > 128) {
        records.delete(id);
        if (latest.get(record.tabId) === id) latest.delete(record.tabId);
      }
    }
  }
  function begin(tab, args) {
    prune();
    const previous = records.get(latest.get(tab.id));
    if (previous) previous.superseded = true;
    const record = { id: `nav_${crypto.randomUUID()}`, tabId: tab.id, created: Date.now(),
      initialURL: tab.url, lastURL: tab.url, initialLoading: tab.status === "loading",
      lastStatus: tab.status, lastPending: tab.pendingUrl,
      requestedURL: args.action === "navigate" ? args.url : null,
      seeded: args.action !== "navigate" || args.url === tab.url,
      initialEnded: false, loadingSeen: false, urlChanged: false, completedURL: null };
    records.set(record.id, record); latest.set(tab.id, record.id); prune();
    return record;
  }
  function update(record, tab, change = {}) {
    if (!record || record.superseded || record.closed || record.failed || record.overridden) return;
    // A second navigation after an observed completion invalidates the old proof.
    if (record.completedURL !== null && (tab.url !== record.completedURL || tab.pendingUrl || tab.status === "loading" || change.status === "loading")) {
      record.overridden = true; return;
    }
    if (typeof tab.url !== "string" || tab.url.length === 0) return;
    if (record.loadingSeen && record.lastStatus === "loading" && change.status === "loading"
      && tab.pendingUrl && record.lastPending && tab.pendingUrl !== record.lastPending) {
      record.overridden = true; return;
    }
    if (!record.seeded) {
      record.seeded = tab.url === record.requestedURL || tab.pendingUrl === record.requestedURL;
      // A different complete URL with no witnessed requested destination could
      // be an unrelated navigation. Do not invent redirect/causality evidence.
      if (!record.seeded) return;
    }
    if (tab.url !== record.initialURL) record.urlChanged = true;
    if (change.status === "loading" || tab.status === "loading") {
      // If go interrupted an already-loading page, identical loading observations
      // alone cannot tell its old load from the new attempt.
      if (!record.initialLoading || record.initialEnded || record.urlChanged) record.loadingSeen = true;
    }
    if (tab.status === "complete" && !tab.pendingUrl) {
      if (record.loadingSeen || record.urlChanged) record.completedURL = tab.url;
      else record.initialEnded = true; // Old complete is never new-navigation proof.
    }
    record.lastURL = tab.url;
    record.lastStatus = tab.status;
    record.lastPending = tab.pendingUrl;
  }
  function install() {
    chrome.tabs.onUpdated?.addListener((tabId, change, tab) => {
      update(records.get(latest.get(tabId)), tab, change);
    });
    chrome.tabs.onRemoved?.addListener((tabId) => {
      for (const record of records.values()) if (record.tabId === tabId) record.closed = true;
    });
  }
  function valid(name, args) {
    const { keys, validTab, integer, text } = shared;
    if (name === "browser.navigation.status") return keys(args, ["tab", "navigation_id"]) && validTab(args.tab)
      && (args.navigation_id === undefined || typeof args.navigation_id === "string" && idPattern.test(args.navigation_id));
    if (!keys(args, ["tab_id", "condition", "url", "baseline_url", "url_baseline_id", "navigation_id", "timeout_ms"])
      || !integer(args.tab_id, 0, Number.MAX_SAFE_INTEGER)
      || args.timeout_ms !== undefined && !integer(args.timeout_ms, 0, 8000)) return false;
    if (args.condition === "url_changed") return args.url === undefined && args.navigation_id === undefined
      && (args.url_baseline_id === undefined ? text(args.baseline_url, 8192)
        : args.baseline_url === undefined && typeof args.url_baseline_id === "string" && baselinePattern.test(args.url_baseline_id));
    if (!["url", "current_complete", "navigation_complete"].includes(args.condition)
      || !text(args.url, 8192) || args.baseline_url !== undefined || args.url_baseline_id !== undefined) return false;
    return args.condition === "navigation_complete"
      ? typeof args.navigation_id === "string" && idPattern.test(args.navigation_id) : args.navigation_id === undefined;
  }
  async function read(id) {
    try { return await chrome.tabs.get(id); }
    catch (error) {
      if (/No tab with id|closed/i.test(error?.message ?? "")) return null;
      throw error;
    }
  }
  function observation(tab, tabId, navigationId) {
    prune();
    const id = navigationId ?? latest.get(tabId), record = records.get(id);
    if (record && record.tabId === tabId) {
      if (!tab) record.closed = true;
      else update(record, tab);
    }
    let navigation = null;
    if (id) {
      const same = record?.tabId === tabId;
      const state = !same ? "missing" : record.superseded ? "superseded" : record.failed ? "failed"
        : record.closed ? "closed" : record.overridden ? "overridden" : record.completedURL !== null ? "complete"
          : record.loadingSeen || record.urlChanged ? "loading" : tab?.status === "complete" ? "unproven" : "awaiting";
      navigation = { navigation_id: id, state, completion_observed: state === "complete" };
    }
    return { tab: tab ? shared.summary(tab) : null, exists: tab !== null,
      url: tab ? Chat2LocalSafety.url(tab.url).url : null,
      pending_url: tab?.pendingUrl ? Chat2LocalSafety.url(tab.pendingUrl).url : null,
      loading_status: !tab ? "closed" : ["loading", "complete"].includes(tab.status) ? tab.status : "unknown", navigation };
  }
  async function status(args, expiresAt) {
    let tab, id = args.tab.tab_id;
    if (id !== undefined) {
      tab = await read(id);
      if (tab && !shared.matches(tab, args.tab)) return shared.fail("page_changed");
    } else {
      const selected = await shared.resolve(args.tab);
      if (!selected.ok) return selected;
      tab = selected.result; id = tab.id;
    }
    if (Date.now() >= expiresAt) return shared.fail("request_expired");
    return { ok: true, result: { ...observation(tab, id, args.navigation_id), url_baseline_id: baseline(tab) } };
  }
  async function wait(args, expiresAt) {
    const started = Date.now(), timeout = args.timeout_ms ?? 5000, deadline = started + timeout;
    let initial = true;
    // 8 seconds / 100ms plus final query. The request deadline remains authoritative.
    for (let attempt = 0; attempt <= 81; attempt++) {
      if (Date.now() >= expiresAt) return shared.fail("request_expired");
      pruneBaselines();
      const reference = args.url_baseline_id === undefined ? null : baselines.get(args.url_baseline_id);
      if (args.url_baseline_id !== undefined && reference?.tabId !== args.tab_id) return shared.fail("url_baseline_unavailable");
      const tab = await read(args.tab_id), observed = observation(tab, args.tab_id, args.navigation_id);
      if (Date.now() >= expiresAt) return shared.fail("request_expired");
      if (reference && (Date.now() - reference.created >= 120000 || baselines.get(args.url_baseline_id) !== reference)) {
        return shared.fail("url_baseline_unavailable");
      }
      // A wait's observation does not mint new records or renew a baseline.
      observed.url_baseline_id = null;
      let reason;
      const state = observed.navigation?.state;
      if (!tab) reason = "tab_closed";
      else if (args.navigation_id && state === "missing") reason = "navigation_missing";
      else if (args.navigation_id && ["superseded", "overridden"].includes(state)) reason = "navigation_overridden";
      else if (args.navigation_id && state === "failed") reason = "navigation_failed";
      const qualifies = tab && typeof tab.url === "string" && tab.url.length > 0 && (args.condition === "url_changed" ? tab.url !== (reference?.url ?? args.baseline_url)
        : tab.url === args.url && (args.condition === "url" || (tab.status === "complete" && !tab.pendingUrl
          && (args.condition === "current_complete" || observed.navigation?.completion_observed === true))));
      const inTime = timeout === 0 ? initial : Date.now() < deadline;
      if (!reason && qualifies && inTime) reason = "condition_met";
      if (!reason && (Date.now() >= deadline || attempt === 81)) reason = args.condition === "navigation_complete"
        && !observed.navigation?.completion_observed ? "navigation_unproven" : "timeout";
      if (reason) return { ok: true, result: { status: reason === "condition_met" ? "matched" : "unmet",
        condition: args.condition, reason, observation: observed, elapsed_ms: Math.max(0, Date.now() - started) } };
      initial = false;
      await new Promise((resolve) => setTimeout(resolve, Math.max(0, Math.min(100, deadline - Date.now(), expiresAt - Date.now()))));
    }
  }
  globalThis.Chat2LocalNavigation = { configure: (helpers) => { shared = helpers; }, valid, begin, install,
    failed: (record) => { record.failed = true; },
    execute: (name, args, expiresAt) => name === "browser.navigation.status" ? status(args, expiresAt) : wait(args, expiresAt) };
})();
