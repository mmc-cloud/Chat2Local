/* Optional, explicitly authorized native group operations; no fallback mutations. */
(() => {
  let shared;
  const colors = ["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"];
  const fields = { create: ["tab_ids", "window_id", "title", "color", "collapsed"], update: ["group_id", "title", "color", "collapsed"],
    move: ["group_id", "window_id", "index"], add_tabs: ["group_id", "tab_ids"], remove_tabs: ["group_id", "tab_ids"] };
  function valid(name, args) {
    const { keys, integer } = shared;
    const id = (v) => integer(v, 0, Number.MAX_SAFE_INTEGER), optional = (v, fn) => v === undefined || fn(v);
    const hash = (v) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
    if (name === "browser.groups.list") return keys(args, ["window_id", "group_id", "offset", "limit", "snapshot", "tab_offset", "tab_limit", "tab_snapshot"])
      && optional(args.window_id, id) && optional(args.group_id, id)
      && optional(args.offset, (v) => integer(v, 0, 10000)) && optional(args.limit, (v) => integer(v, 1, 50))
      && optional(args.tab_offset, (v) => integer(v, 0, 10000)) && optional(args.tab_limit, (v) => integer(v, 1, 50))
      && optional(args.snapshot, hash) && optional(args.tab_snapshot, hash)
      && ((args.offset ?? 0) === 0 || args.snapshot !== undefined)
      && ((args.tab_offset ?? 0) === 0 || args.group_id !== undefined && args.tab_snapshot !== undefined)
      && (args.tab_snapshot === undefined || args.group_id !== undefined);
    if (!args || !Object.hasOwn(fields, args.action) || !keys(args, ["action", ...fields[args.action]])) return false;
    if (args.action !== "create" && !id(args.group_id)) return false;
    if (["create", "add_tabs", "remove_tabs"].includes(args.action)
      && (!Array.isArray(args.tab_ids) || args.tab_ids.length < 1 || args.tab_ids.length > 50
        || new Set(args.tab_ids).size !== args.tab_ids.length || !args.tab_ids.every(id))) return false;
    if (args.action === "move" && !integer(args.index, -1, 2 ** 31 - 1)) return false;
    if (args.action === "update" && !["title", "color", "collapsed"].some((key) => args[key] !== undefined)) return false;
    return optional(args.window_id, id) && optional(args.title, (v) => typeof v === "string" && Array.from(v).length <= 256)
      && optional(args.color, (v) => colors.includes(v)) && optional(args.collapsed, (v) => typeof v === "boolean");
  }
  async function allowed() {
    const { groupAccess = false } = await chrome.storage.local.get("groupAccess");
    return groupAccess === true && await chrome.permissions.contains({ permissions: ["tabGroups"] });
  }
  async function get(id) {
    try { return await chrome.tabGroups.get(id); }
    catch (error) { if (/No group with id/i.test(error?.message ?? "")) return null; throw error; }
  }
  const ordered = (tabs) => [...tabs].sort((a, b) => a.index - b.index);
  async function hash(value) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(value)));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  async function summary(group, offset = 0, limit = 50, expected, observedTabs) {
    const tabs = ordered(observedTabs ?? await chrome.tabs.query({ groupId: group.id }));
    if (tabs.length > 10000) return shared.fail("result_too_large");
    if (tabs.some((tab) => tab.groupId !== group.id || tab.windowId !== group.windowId)) return shared.fail("group_changed");
    const tabIds = tabs.map((tab) => tab.id), snapshot = await hash([group.id, group.windowId, tabIds]);
    if (expected !== undefined && expected !== snapshot) return shared.fail("group_changed");
    if (offset > tabs.length) return shared.fail("invalid_arguments");
    const page = tabs.slice(offset, offset + limit), next = offset + page.length;
    return { ok: true, result: { group_id: group.id, window_id: group.windowId,
      title: Chat2LocalSafety.text(group.title, 256).text, color: group.color, collapsed: group.collapsed,
      tab_ids: tabIds, tabs: page.map(shared.managedTab), total_tabs: tabs.length,
      tab_offset: offset, tab_limit: limit, has_more: next < tabs.length,
      next_offset: next < tabs.length ? next : null, tab_snapshot: snapshot } };
  }
  async function list(args, expiresAt) {
    if (!await allowed()) return shared.fail("groups_permission_denied");
    if (args.window_id !== undefined) {
      const window = await shared.normalWindow(args.window_id);
      if (!window.ok) return window;
    }
    let groups;
    if (args.group_id !== undefined) {
      const group = await get(args.group_id);
      if (!group || args.window_id !== undefined && group.windowId !== args.window_id) return shared.fail("group_not_found");
      groups = [group];
    } else groups = await chrome.tabGroups.query(args.window_id === undefined ? {} : { windowId: args.window_id });
    groups.sort((a, b) => a.id - b.id);
    if (groups.length > 10000) return shared.fail("result_too_large");
    const snapshot = await hash([args.window_id ?? null, args.group_id ?? null, groups.map((g) => [g.id, g.windowId])]);
    if (args.snapshot !== undefined && snapshot !== args.snapshot) return shared.fail("group_changed");
    const offset = args.offset ?? 0, limit = args.limit ?? 20;
    if (offset > groups.length) return shared.fail("invalid_arguments");
    const summaries = [];
    for (const group of groups.slice(offset, offset + limit)) {
      const result = await summary(group, args.tab_offset, args.tab_limit, args.tab_snapshot);
      if (!result.ok) return result;
      summaries.push(result.result);
    }
    if (!await allowed()) return shared.fail("groups_permission_denied");
    if (Date.now() >= expiresAt) return shared.fail("request_expired");
    const next = offset + summaries.length;
    return shared.boundedResult({ groups: summaries, total: groups.length, offset, has_more: next < groups.length,
      next_offset: next < groups.length ? next : null, snapshot });
  }
  const sameTab = (a, b) => a.id === b.id && a.windowId === b.windowId && a.groupId === b.groupId && a.pinned === b.pinned;
  const contiguous = (tabs, group) => tabs.length > 0 && tabs.every((tab, index) => tab.groupId === group.id
    && tab.windowId === group.windowId && !tab.pinned && Number.isInteger(tab.index) && tab.index >= 0
    && tab.index === tabs[0].index + index);
  const layout = (tabs) => JSON.stringify(tabs.map((tab) => [tab.id, tab.groupId, tab.pinned]));
  function moveTargets(tabs, groupId, requested) {
    const rest = tabs.filter((tab) => tab.groupId !== groupId);
    // Verify only the insertion boundary, not Chrome's sorting implementation.
    // Oversized/-1 indices denote the end; pinned tabs and another group can
    // constrain a slot to their edges. Chrome may also reject such requests.
    const index = requested < 0 ? rest.length : Math.min(requested, rest.length);
    const pinned = rest.findIndex((tab) => !tab.pinned);
    const first = pinned < 0 ? rest.length : pinned;
    if (index < first) return [first];
    const other = rest[index]?.groupId;
    if (other !== undefined && other !== -1 && other === rest[index - 1]?.groupId) {
      return [rest.findIndex((tab) => tab.groupId === other), rest.findLastIndex((tab) => tab.groupId === other) + 1];
    }
    return [index];
  }
  async function manage(args, expiresAt) {
    const steps = [], ids = args.tab_ids ?? [];
    let groupId = args.group_id, attempted = false, moveBefore, movedGroup, actualIndex = null;
    const failed = (code) => shared.fail(code, attempted ? { progress: {
      completed_steps: [...steps], outcome: steps.length ? "partial" : "unknown",
      ...(groupId === undefined ? {} : { group_id: groupId }), tab_ids: [...ids],
    } } : undefined);
    async function gate() {
      if (!await allowed()) return "groups_permission_denied";
      return Date.now() >= expiresAt ? "request_expired" : null;
    }
    try {
      let blocked = await gate();
      if (blocked) return failed(blocked);
      let group = groupId === undefined ? null : await get(groupId);
      if (groupId !== undefined && !group) return failed("group_not_found");
      const membersBefore = group ? ordered(await chrome.tabs.query({ groupId })).map((tab) => tab.id) : null;
      if (membersBefore && membersBefore.length > 10000) return failed("result_too_large");
      const before = [];
      for (const id of ids) {
        try { before.push(await chrome.tabs.get(id)); }
        catch (error) { if (/No tab with id|closed/i.test(error?.message ?? "")) return failed("tab_not_found"); throw error; }
      }
      if (args.action === "remove_tabs" && before.some((tab) => tab.groupId !== groupId)) return failed("group_changed");
      const windows = new Set(before.map((tab) => tab.windowId));
      if (group) windows.add(group.windowId);
      if (args.window_id !== undefined) windows.add(args.window_id);
      const windowTabs = new Map();
      for (const id of windows) {
        const window = await shared.normalWindow(id);
        if (!window.ok) return failed(window.error.code);
        windowTabs.set(id, ordered(window.result.tabs));
      }
      if (args.action === "create" && args.window_id === undefined && windows.size !== 1) return failed("invalid_arguments");
      // Every explicit member is re-read after asynchronous permission/window checks.
      for (const tab of before) {
        const current = await chrome.tabs.get(tab.id);
        if (!sameTab(tab, current)) return failed("group_changed");
      }
      if (group) {
        const current = await get(groupId);
        if (!current || current.windowId !== group.windowId) return failed("group_changed");
        const membersNow = ordered(await chrome.tabs.query({ groupId }));
        if (JSON.stringify(membersNow.map((tab) => tab.id)) !== JSON.stringify(membersBefore)) return failed("group_changed");
        group = current;
        if (args.action === "move") {
          const source = windowTabs.get(group.windowId), destination = windowTabs.get(args.window_id ?? group.windowId);
          if (source.length > 10000 || destination.length > 10000) return failed("result_too_large");
          const original = source.filter((tab) => tab.groupId === groupId);
          if (!contiguous(membersNow, group) || !contiguous(original, group) || original.length !== membersNow.length
            || membersNow.some((tab, i) => !sameTab(tab, original[i]) || tab.index !== original[i].index)) return failed("group_changed");
          moveBefore = { index: membersNow[0].index, windowId: group.windowId,
            destination, targets: moveTargets(destination, groupId, args.index) };
        }
      }
      blocked = await gate();
      if (blocked) return failed(blocked);
      if (args.action === "create" || args.action === "add_tabs") {
        attempted = true;
        groupId = await chrome.tabs.group({ tabIds: ids, ...(args.action === "create"
          ? { createProperties: { windowId: args.window_id ?? before[0].windowId } } : { groupId }) });
        steps.push("grouped");
        if (args.action === "add_tabs" && groupId !== args.group_id) return failed("state_not_reached");
      } else if (args.action === "remove_tabs") {
        attempted = true;
        await chrome.tabs.ungroup(ids); steps.push("ungrouped");
      } else if (args.action === "move") {
        attempted = true;
        movedGroup = await chrome.tabGroups.move(groupId, { index: args.index, windowId: args.window_id ?? group.windowId }); steps.push("moved");
      }
      const properties = Object.fromEntries(["title", "color", "collapsed"].filter((key) => args[key] !== undefined).map((key) => [key, args[key]]));
      if (["create", "update"].includes(args.action) && Object.keys(properties).length) {
        blocked = await gate();
        if (blocked) return failed(blocked);
        attempted = true;
        await chrome.tabGroups.update(groupId, properties); steps.push("updated");
      }
      blocked = await gate();
      if (blocked) return failed(blocked);
      const actual = await get(groupId);
      const observedTabs = [];
      for (const id of ids) observedTabs.push(await chrome.tabs.get(id));
      if (args.action === "remove_tabs") {
        if (observedTabs.some((tab) => tab.groupId !== -1)) return failed("state_not_reached");
      } else {
        if (!actual) return failed("state_not_reached");
        if (observedTabs.some((tab) => tab.groupId !== groupId || tab.windowId !== actual.windowId)) return failed("state_not_reached");
        if (Object.entries(properties).some(([key, value]) => (actual[key] ?? (key === "title" ? "" : undefined)) !== value)) return failed("state_not_reached");
        if (args.action === "move" && actual.windowId !== (args.window_id ?? group.windowId)) return failed("state_not_reached");
      }
      let result = null;
      if (actual) {
        let members;
        if (args.action === "move") {
          members = ordered(await chrome.tabs.query({ groupId }));
          const destination = await shared.normalWindow(args.window_id ?? moveBefore.windowId);
          if (!destination.ok) return failed(destination.error.code);
          const strip = ordered(destination.result.tabs);
          if (strip.length > 10000) return failed("result_too_large");
          const inStrip = strip.filter((tab) => tab.groupId === groupId);
          // The native receipt is optional; observed state is always required.
          if (actual.id !== groupId || (movedGroup !== undefined && (movedGroup?.id !== groupId || movedGroup.windowId !== actual.windowId))
            || !contiguous(members, actual) || JSON.stringify(members.map((tab) => tab.id)) !== JSON.stringify(membersBefore)
            || layout(strip.filter((tab) => tab.groupId !== groupId)) !== layout(moveBefore.destination.filter((tab) => tab.groupId !== groupId))
            || strip.some((tab, index) => tab.index !== index) || inStrip.length !== members.length
            || members.some((tab, index) => !sameTab(tab, inStrip[index]) || tab.index !== inStrip[index].index)) return failed("state_not_reached");
          actualIndex = members[0].index;
          if (!moveBefore.targets.includes(actualIndex)) return failed("state_not_reached");
        }
        const current = await summary(actual, 0, 50, undefined, members);
        if (!current.ok) return failed(current.error.code);
        result = current.result;
        if (args.action === "move" && (result.tab_ids.length !== membersBefore.length
          || !membersBefore.every((id) => result.tab_ids.includes(id)))) return failed("state_not_reached");
      }
      blocked = await gate();
      if (blocked) return failed(blocked);
      const response = shared.boundedResult({ action: args.action, group: result,
        tab_ids: ids.length ? ids : result?.tab_ids ?? [], actual_index: actualIndex, completed_steps: steps });
      return response.ok ? response : failed(response.error.code);
    } catch { return failed("browser_operation_failed"); }
  }
  globalThis.Chat2LocalGroups = { configure: (helpers) => { shared = helpers; }, valid,
    execute: (name, args, expiresAt) => name === "browser.groups.list" ? list(args, expiresAt) : manage(args, expiresAt) };
})();
