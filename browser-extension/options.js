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
      const current = await chrome.storage.local.get("pageOrigins");
      await chrome.storage.local.set({ pageOrigins: (current.pageOrigins || []).filter((value) => value !== pattern) });
      // Keep the required loopback host permission used by the bridge.
      if (pattern !== "http://127.0.0.1/*") await chrome.permissions.remove({ origins: [pattern] });
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
