import { fixture } from "./operations-helper.mjs";

export function browser({ granted = true, consent = true, ...fixtureOptions } = {}) {
  const tabs = [1, 2, 3].map((id) => ({ id, windowId: id === 3 ? 8 : 7, index: id === 2 ? 1 : 0,
    active: id === 1, pinned: false, groupId: -1, status: "complete", title: "token=PRIVATE-TITLE", url: `https://test.example/${id}` }));
  const f = fixture({ tabs, ...fixtureOptions });
  const groups = [], mutations = [], permissions = { granted };
  f.settings.groupAccess = consent;
  f.chrome.permissions.contains = async ({ permissions: names, origins }) => names ? permissions.granted : Boolean(origins);
  let updateListener, removeListener;
  f.chrome.tabs.onUpdated = { addListener(fn) { updateListener = fn; } };
  f.chrome.tabs.onRemoved = { addListener(fn) { removeListener = fn; } };
  f.navigation.install();
  const emit = (id, change) => {
    const tab = tabs.find((item) => item.id === id);
    for (const [key, value] of Object.entries(change)) { if (value === undefined) delete tab[key]; else tab[key] = value; }
    updateListener?.(id, { ...change }, { ...tab });
  };
  const close = (id) => { tabs.splice(tabs.findIndex((tab) => tab.id === id), 1); removeListener?.(id); };
  const queryTabs = (query = {}) => tabs.filter((tab) => (query.groupId === undefined || tab.groupId === query.groupId)
    && (query.windowId === undefined || tab.windowId === query.windowId)).map((tab) => ({ ...tab }));
  f.chrome.tabs.query = async (query) => queryTabs(query);
  f.chrome.tabs.update = async (id, args) => { mutations.push(["tabs.update", id, { ...args }]); return { ...tabs.find((tab) => tab.id === id) }; };
  for (const method of ["reload", "goBack", "goForward"]) f.chrome.tabs[method] = async (id) => { mutations.push([method, id]); };
  f.chrome.windows = { get: async (id) => {
    if (![7, 8, 9].includes(id)) throw Error(`No window with id: ${id}.`);
    return { id, type: id === 9 ? "popup" : "normal", tabs: queryTabs({ windowId: id }) };
  } };
  const collectEmpty = () => { for (let i = groups.length - 1; i >= 0; i--) if (!tabs.some((tab) => tab.groupId === groups[i].id)) groups.splice(i, 1); };
  f.chrome.tabGroups = {
    get: async (id) => { const group = groups.find((item) => item.id === id); if (!group) throw Error(`No group with id: ${id}.`); return { ...group }; },
    query: async (query) => groups.filter((group) => query.windowId === undefined || group.windowId === query.windowId).map((group) => ({ ...group })),
    update: async (id, args) => { mutations.push(["groups.update", id, { ...args }]); Object.assign(groups.find((g) => g.id === id), args); return f.chrome.tabGroups.get(id); },
    move: async (id, args) => {
      mutations.push(["groups.move", id, { ...args }]);
      const group = groups.find((item) => item.id === id), source = group.windowId;
      const ordered = (items) => items.sort((a, b) => a.index - b.index);
      const members = ordered(tabs.filter((tab) => tab.groupId === id));
      const remaining = ordered(tabs.filter((tab) => tab.windowId === args.windowId && tab.groupId !== id));
      const start = args.index === -1 ? remaining.length : Math.min(args.index, remaining.length);
      if (remaining[start]?.pinned) throw Error("Cannot move the group into pinned tabs");
      if (remaining[start]?.groupId !== undefined && remaining[start].groupId !== -1
        && remaining[start].groupId === remaining[start - 1]?.groupId) throw Error("Cannot split another group");
      remaining.splice(start, 0, ...members);
      remaining.forEach((tab, index) => { tab.windowId = args.windowId; tab.index = index; });
      if (source !== args.windowId) ordered(tabs.filter((tab) => tab.windowId === source)).forEach((tab, index) => { tab.index = index; });
      group.windowId = args.windowId;
      return { ...group };
    },
  };
  f.chrome.tabs.group = async (args) => {
    mutations.push(["tabs.group", args]);
    const selected = args.tabIds.map((id) => tabs.find((tab) => tab.id === id));
    if (selected.some((tab) => !tab || tab.pinned)) throw Error("Cannot group pinned or missing tabs PRIVATE-DETAIL");
    let group = groups.find((g) => g.id === args.groupId);
    if (!group) { group = { id: Math.max(100, ...groups.map((g) => g.id)) + 1, windowId: args.createProperties.windowId,
      title: "", color: "grey", collapsed: false }; groups.push(group); }
    selected.forEach((tab) => { tab.groupId = group.id; tab.windowId = group.windowId; });
    collectEmpty();
    return group.id;
  };
  f.chrome.tabs.ungroup = async (ids) => { mutations.push(["tabs.ungroup", [...ids]]); ids.forEach((id) => { tabs.find((tab) => tab.id === id).groupId = -1; }); collectEmpty(); };
  const call = f.call;
  const go = (id = 1, action = "navigate", url = "https://next.example/") => call("browser.navigation.go", {
    action, tab: { tab_id: id }, ...(action === "navigate" ? { url } : {}),
  });
  const status = (id = 1, navigation_id) => call("browser.navigation.status", { tab: { tab_id: id }, ...(navigation_id ? { navigation_id } : {}) });
  const wait = (args = {}) => call("browser.navigation.wait", { tab_id: 1, timeout_ms: 250, ...args });
  const manage = (args) => call("browser.groups.manage", args);
  const list = (args = {}) => call("browser.groups.list", args);
  return { ...f, groups, mutations, permissions, emit, close, go, status, wait, manage, list };
}
