const form = document.querySelector("#settings");
const enabled = document.querySelector("#enabled");
const port = document.querySelector("#port");
const token = document.querySelector("#token");
document.querySelector("#extension-id").textContent = chrome.runtime.id;

async function load() {
  const { connection } = await chrome.storage.local.get("connection");
  enabled.checked = connection?.enabled === true;
  port.value = connection?.port ?? 8766;
  token.value = connection?.token ?? "";
  const { connectionStatus } = await chrome.storage.session.get("connectionStatus");
  document.querySelector("#status").textContent = connectionStatus ?? "尚未连接";
}
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(token.value)) {
    document.querySelector("#saved").textContent = "凭据必须为 32–128 位字母、数字、下划线或连字符。";
    return;
  }
  await chrome.storage.local.set({ connection: {
    enabled: enabled.checked, port: Number(port.value), token: token.value,
  } });
  document.querySelector("#saved").textContent = "已保存。";
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.connectionStatus) {
    document.querySelector("#status").textContent = changes.connectionStatus.newValue;
  }
});
void load();

const websiteForm = document.querySelector("#website-permission");
const permissionStatus = document.querySelector("#permission-status");
async function showWebsites() {
  const { pageOrigins = [] } = await chrome.storage.local.get("pageOrigins");
  const list = document.querySelector("#authorized-websites");
  list.replaceChildren();
  for (const pattern of Array.isArray(pageOrigins) ? pageOrigins : []) {
    const item = document.createElement("li");
    item.textContent = pattern + " ";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "撤销授权";
    remove.addEventListener("click", async () => {
      const current = await chrome.storage.local.get(["pageOrigins", "allWebsites"]);
      await chrome.storage.local.set({ pageOrigins: (current.pageOrigins || []).filter((value) => value !== pattern) });
      // Keep the required loopback host permission used by the bridge.
      if (pattern !== "http://127.0.0.1/*" && current.allWebsites !== true) await chrome.permissions.remove({ origins: [pattern] });
      await showWebsites();
    });
    item.append(remove);
    list.append(item);
  }
}
websiteForm.addEventListener("submit", (event) => {
  event.preventDefault();
  let pattern;
  try { pattern = globalThis.Chat2LocalBrowser.pagePattern(document.querySelector("#website-url").value); }
  catch { pattern = null; }
  if (!pattern) { permissionStatus.textContent = "仅支持普通 HTTP(S) 网站。"; return; }
  // Call synchronously inside this real user gesture, before any await.
  void chrome.permissions.request({ origins: [pattern] }).then(async (granted) => {
    if (!granted) { permissionStatus.textContent = "未获得网站权限。"; return; }
    const { pageOrigins = [] } = await chrome.storage.local.get("pageOrigins");
    await chrome.storage.local.set({ pageOrigins: [...new Set([...pageOrigins, pattern])] });
    permissionStatus.textContent = "已授权。";
    await showWebsites();
  }).catch(() => { permissionStatus.textContent = "授权失败，请重新点击按钮。"; });
});
void showWebsites();

const allStatus = document.querySelector("#all-websites-status");
async function showAllWebsites() {
  const { allWebsites = false } = await chrome.storage.local.get("allWebsites");
  const granted = await chrome.permissions.contains({ origins: globalThis.Chat2LocalBrowser.allWebsiteOrigins });
  allStatus.textContent = allWebsites === true && granted
    ? "全站授权已开启 / All-site access is enabled"
    : "全站授权未开启（按单站记录检查） / All-site access is off (individual grants apply)";
}
document.querySelector("#allow-all-websites").addEventListener("click", () => {
  // Preserve the real user gesture; never request permission from an MCP command.
  void chrome.permissions.request({ origins: globalThis.Chat2LocalBrowser.allWebsiteOrigins }).then(async (granted) => {
    if (!granted) { allStatus.textContent = "未获得全站权限 / All-site permission was not granted"; return; }
    await chrome.storage.local.set({ allWebsites: true });
    await showAllWebsites();
  }).catch(() => { allStatus.textContent = "全站授权失败，请重试 / Could not grant all-site access; try again"; });
});
document.querySelector("#revoke-all-websites").addEventListener("click", () => {
  void (async () => {
    await chrome.storage.local.set({ allWebsites: false });
    const removed = await chrome.permissions.remove({ origins: globalThis.Chat2LocalBrowser.allWebsiteOrigins });
    if (!removed) throw Error("Permission removal failed");
    allStatus.textContent = "已撤销全站授权。单站记录保留，需要时重新点击授权。 / All-site access revoked. Individual entries remain; grant them again if needed.";
  })().catch(() => { allStatus.textContent = "撤销未完成，请重试并检查 Chrome 网站权限 / Revocation incomplete; retry and check Chrome site access"; });
});
void showAllWebsites().catch(() => { allStatus.textContent = "无法读取网站权限 / Could not read website permissions"; });

const groupStatus = document.querySelector("#tab-groups-status");
async function showGroups() {
  const { groupAccess = false } = await chrome.storage.local.get("groupAccess");
  const granted = await chrome.permissions.contains({ permissions: ["tabGroups"] });
  groupStatus.textContent = groupAccess === true && granted
    ? "标签分组已授权 / Tab groups enabled" : "标签分组未授权 / Tab groups disabled";
}
document.querySelector("#allow-tab-groups").addEventListener("click", () => {
  // Keep request in the actual Options-page gesture, before any asynchronous work.
  void chrome.permissions.request({ permissions: ["tabGroups"] }).then(async (granted) => {
    if (!granted) { groupStatus.textContent = "未获得分组权限 / Tab group permission denied"; return; }
    await chrome.storage.local.set({ groupAccess: true });
    await showGroups();
  }).catch(() => { groupStatus.textContent = "分组授权失败，请重试 / Tab group grant failed; try again"; });
});
document.querySelector("#revoke-tab-groups").addEventListener("click", () => {
  void (async () => {
    // Disable the application gate first, even if Chrome revocation then fails.
    await chrome.storage.local.set({ groupAccess: false });
    if (!await chrome.permissions.remove({ permissions: ["tabGroups"] })) throw Error("Permission removal failed");
    await showGroups();
  })().catch(() => { groupStatus.textContent = "已停用分组；Chrome 权限撤销未完成 / Groups disabled; Chrome revocation incomplete"; });
});
const refreshGroups = () => { void showGroups().catch(() => { groupStatus.textContent = "无法读取分组权限 / Could not read tab group permission"; }); };
chrome.permissions.onAdded?.addListener(refreshGroups);
chrome.permissions.onRemoved?.addListener(refreshGroups);
refreshGroups();

const bookmarkStatus = document.querySelector("#bookmarks-status");
async function showBookmarks() {
  const { bookmarkAccess = false } = await chrome.storage.local.get("bookmarkAccess");
  const granted = await chrome.permissions.contains({ permissions: ["bookmarks"] });
  bookmarkStatus.textContent = bookmarkAccess === true && granted
    ? "书签已授权 / Bookmarks enabled" : "书签未授权 / Bookmarks disabled";
}
document.querySelector("#allow-bookmarks").addEventListener("click", () => {
  // Preserve the user gesture: MCP and background requests never grant access.
  void chrome.permissions.request({ permissions: ["bookmarks"] }).then(async (granted) => {
    if (!granted) { bookmarkStatus.textContent = "未获得书签权限 / Bookmark permission denied"; return; }
    await chrome.storage.local.set({ bookmarkAccess: true });
    await showBookmarks();
  }).catch(() => { bookmarkStatus.textContent = "书签授权失败，请重试 / Bookmark grant failed; try again"; });
});
document.querySelector("#revoke-bookmarks").addEventListener("click", () => {
  void (async () => {
    await chrome.storage.local.set({ bookmarkAccess: false });
    if (!await chrome.permissions.remove({ permissions: ["bookmarks"] })) throw Error("Permission removal failed");
    await showBookmarks();
  })().catch(() => { bookmarkStatus.textContent = "已停用书签；Chrome 权限撤销未完成 / Bookmarks disabled; Chrome revocation incomplete"; });
});
const refreshBookmarks = () => { void showBookmarks().catch(() => { bookmarkStatus.textContent = "无法读取书签权限 / Could not read bookmark permission"; }); };
chrome.permissions.onAdded?.addListener(refreshBookmarks);
chrome.permissions.onRemoved?.addListener(refreshBookmarks);
refreshBookmarks();
