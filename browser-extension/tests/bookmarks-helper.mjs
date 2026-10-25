import { fixture } from "./operations-helper.mjs";

export function bookmarks({ granted = true, consent = true, ...options } = {}) {
  const f = fixture(options), nodes = new Map(), mutations = [], permission = { granted };
  let sequence = 0, clock = 1000;
  f.settings.bookmarkAccess = consent;
  f.chrome.permissions.contains = async ({ permissions }) => permissions?.includes("bookmarks") ? permission.granted : true;
  f.chrome.permissions.request = async () => { throw Error("MCP must never request permission"); };
  const raw = (id) => { const node = nodes.get(id); if (!node) throw Error("Can't find bookmark for id. PRIVATE-ERROR"); return node; };
  const shallow = (node) => Object.fromEntries(Object.entries(node).filter(([key]) => key !== "children"));
  const children = (id) => raw(id).children.map((id) => raw(id));
  const reindex = (id) => children(id).forEach((node, index) => { node.index = index; node.parentId = id; });
  const touch = (id) => { raw(id).dateGroupModified = ++clock; reindex(id); };
  const canonicalURL = (value) => {
    const parsed = new URL(value);
    if (["chrome:", "chrome-extension:"].includes(parsed.protocol)) {
      parsed.hostname = parsed.hostname.toLowerCase();
      if (!parsed.pathname) parsed.pathname = "/";
    }
    return parsed.href;
  };
  function add(id, parentId, extra = {}) {
    const node = { id, title: "Same", dateAdded: ++clock, ...(parentId === undefined ? {} : { parentId }), ...extra };
    if (node.url === undefined) { node.children = []; node.dateGroupModified ??= clock; }
    nodes.set(id, node);
    if (parentId !== undefined) { raw(parentId).children.push(id); touch(parentId); }
    return node;
  }
  add("root", undefined, { title: "Root" });
  add("bar", "root", { folderType: "bookmarks-bar" });
  add("other", "root", { folderType: "other" });
  add("managed", "root", { folderType: "managed", unmodifiable: "managed" });
  add("policy", "managed", { url: "https://policy.example/" });
  add("folder", "bar"); add("empty", "bar");
  add("bookmark", "bar", { url: "https://example.com/?token=PRIVATE-URL" });
  add("samefolder", "bar");
  add("specialurl", "bar", { url: "javascript:alert('PRIVATE-LEGACY')" });
  add("a", "folder", { title: "token=PRIVATE-TITLE", url: "https://example.com/a?token=PRIVATE-URL" });
  add("nested", "folder"); add("b", "nested", { url: "https://example.com/b#token=PRIVATE-FRAGMENT" });
  const clone = (node) => ({ ...shallow(node), ...(node.children ? { children: node.children.map((id) => clone(raw(id))) } : {}) });
  const writable = (node, container = false) => {
    if (!node.parentId || !container && (node.folderType || raw(node.parentId).parentId === undefined)) throw Error("Protected node PRIVATE-ERROR");
    for (let current = node; current; current = current.parentId ? raw(current.parentId) : null) {
      if (current.unmodifiable) throw Error("Managed node PRIVATE-ERROR");
    }
    if (container && !node.children) throw Error("Not a folder PRIVATE-ERROR");
  };
  const erase = (node) => {
    for (const id of [...(node.children ?? [])]) erase(raw(id));
    if (node.parentId) { const parent = raw(node.parentId); parent.children.splice(parent.children.indexOf(node.id), 1); touch(parent.id); }
    nodes.delete(node.id);
  };
  f.chrome.bookmarks = {
    get: async (id) => [shallow(raw(id))],
    getChildren: async (id) => children(id).map(shallow),
    getTree: async () => [clone(raw("root"))],
    getSubTree: async (id) => [clone(raw(id))],
    search: async (filters) => [...nodes.values()].filter((node) => (filters.query === undefined
      || (node.title + " " + (node.url ?? "")).toLowerCase().includes(filters.query.toLowerCase()))
      && (filters.title === undefined || node.title === filters.title) && (filters.url === undefined || node.url === filters.url)).map(shallow),
    getRecent: async (count) => [...nodes.values()].filter((node) => node.url !== undefined).sort((a, b) => b.dateAdded - a.dateAdded).slice(0, count).map(shallow),
    create: async (args) => {
      mutations.push(["create", { ...args }]); const parent = raw(args.parentId); writable(parent, true);
      if (args.index !== undefined && args.index > parent.children.length) throw Error("Invalid index PRIVATE-ERROR");
      const node = add(`new${++sequence}`, parent.id, { title: args.title, ...(args.url === undefined ? {} : { url: canonicalURL(args.url) }) });
      if (args.index !== undefined) { parent.children.pop(); parent.children.splice(args.index, 0, node.id); touch(parent.id); }
      return shallow(node);
    },
    update: async (id, args) => {
      mutations.push(["update", id, { ...args }]); const node = raw(id); writable(node);
      if (args.url !== undefined && node.children) throw Error("Folder URL PRIVATE-ERROR");
      Object.assign(node, args, args.url === undefined ? {} : { url: canonicalURL(args.url) });
      return shallow(node);
    },
    move: async (id, args) => {
      mutations.push(["move", id, { ...args }]); const node = raw(id), parent = raw(args.parentId); writable(node); writable(parent, true);
      for (let current = parent; current; current = current.parentId ? raw(current.parentId) : null) if (current.id === id) throw Error("Cycle PRIVATE-ERROR");
      let index = args.index ?? parent.children.length;
      if (index > parent.children.length) throw Error("Invalid index PRIVATE-ERROR");
      const oldParent = raw(node.parentId), oldIndex = node.index;
      if (oldParent.id === parent.id && index > oldIndex) index--;
      oldParent.children.splice(oldIndex, 1); touch(oldParent.id);
      parent.children.splice(index, 0, id); touch(parent.id);
      return shallow(node);
    },
    remove: async (id) => { mutations.push(["remove", id]); const node = raw(id); writable(node);
      if (node.children?.length) throw Error("Nonempty PRIVATE-ERROR"); erase(node); },
    removeTree: async (id) => { mutations.push(["removeTree", id]); const node = raw(id); writable(node); erase(node); },
  };
  return { ...f, nodes, mutations, permission, add, erase,
    query: (args = {}) => f.call("browser.bookmarks.query", { action: "roots", ...args }),
    manage: (args) => f.call("browser.bookmarks.manage", args),
    preview: (args = {}) => f.call("browser.bookmarks.preview_remove_tree", { folder_id: "folder", ...args }),
    removeTree: (preview_id, folder_id = "folder") => f.call("browser.bookmarks.remove_tree", { folder_id, preview_id }),
  };
}
