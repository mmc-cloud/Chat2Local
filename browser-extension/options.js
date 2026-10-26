const $ = (selector) => document.querySelector(selector);
const form = $("#settings");
const enabled = $("#enabled");
const port = $("#port");
const token = $("#token");
const fields = $("#connection-fields");
const browser = globalThis.Chat2LocalBrowser;
const tokenPattern = /^[A-Za-z0-9_-]{32,128}$/;
let savedEnabled = false;
let hasToken = false;
let connectionState;
let configLoaded = false;

function message(selector, text, tone = "neutral") {
  const element = $(selector);
  element.textContent = text;
  element.dataset.tone = tone;
}

const theme = $("#theme");
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
function applyTheme() {
  document.documentElement.dataset.theme = theme.value === "system"
    ? (systemTheme.matches ? "dark" : "light") : theme.value;
}
try {
  const preference = localStorage.getItem("optionsTheme");
  theme.value = ["light", "dark"].includes(preference) ? preference : "system";
} catch {
  message("#theme-feedback", "无法读取主题偏好，已跟随系统。", "warning");
}
applyTheme();
systemTheme.addEventListener("change", applyTheme);
theme.addEventListener("change", () => {
  applyTheme();
  try {
    // Keep this page-only preference separate from connection and permission storage.
    localStorage.setItem("optionsTheme", theme.value);
    message("#theme-feedback", "主题已切换，偏好已保存。", "success");
  } catch {
    message("#theme-feedback", "主题已切换，但无法保存偏好；重新打开页面后将跟随系统。", "warning");
  }
});

function showConnection() {
  const states = {
    connected: ["已连接", "success"],
    connecting: ["连接中", "info"],
    disconnected: ["未连接 · 等待重连", "neutral"],
    connection_failed: ["连接异常 · 将自动重试", "error"],
    disabled_or_unconfigured: [!hasToken ? "未连接 · 尚未配置"
      : savedEnabled ? "未连接 · 请检查连接配置" : "未连接 · 连接已停用", "neutral"],
  };
  const fallback = savedEnabled && hasToken ? "未连接" : hasToken ? "未连接 · 连接已停用" : "未连接 · 尚未配置";
  const [text, tone] = Object.hasOwn(states, connectionState) ? states[connectionState] : [fallback, "neutral"];
  message("#status", text, tone);
}

function showTokenState(connection) {
  savedEnabled = connection?.enabled === true;
  hasToken = typeof connection?.token === "string" && tokenPattern.test(connection.token);
  $("#token-state").textContent = hasToken ? "已配置" : "未配置";
  token.placeholder = hasToken ? "留空保留现有凭据，输入内容则替换" : "输入浏览器独立凭据";
  token.required = !hasToken;
  $("#token-hint").textContent = hasToken
    ? "现有凭据不会显示。修改端口或启停连接时留空即可保留；输入新凭据后保存以替换。"
    : "使用 32–128 位字母、数字、下划线或连字符。请使用浏览器专用凭据。";
  showConnection();
}

async function loadConnection() {
  try {
    const { connection } = await chrome.storage.local.get("connection");
    enabled.checked = connection?.enabled === true;
    port.value = connection?.port ?? 8766;
    // Never put the saved credential in the DOM, even in a password input.
    showTokenState(connection);
    configLoaded = true;
    fields.disabled = false;
  } catch {
    message("#saved", "无法读取连接设置，请重新打开此页面后重试。", "error");
  }
  try {
    const { connectionStatus } = await chrome.storage.session.get("connectionStatus");
    connectionState = connectionStatus;
    showConnection();
  } catch {
    message("#status", "连接状态读取失败", "error");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!configLoaded || fields.disabled) return;
  const nextPort = Number(port.value);
  if (!Number.isInteger(nextPort) || nextPort < 1 || nextPort > 65535) {
    message("#saved", "端口必须为 1–65535 的整数。", "error");
    return;
  }
  fields.disabled = true;
  message("#saved", "正在保存…", "info");
  try {
    // Read the latest saved token so another Options window cannot leave us with a stale copy.
    const { connection } = await chrome.storage.local.get("connection");
    const nextToken = token.value || connection?.token || "";
    if (!tokenPattern.test(nextToken)) {
      message("#saved", "凭据必须为 32–128 位字母、数字、下划线或连字符。", "error");
      return;
    }
    const next = { enabled: enabled.checked, port: nextPort, token: nextToken };
    await chrome.storage.local.set({ connection: next });
    token.value = "";
    showTokenState(next);
    message("#saved", "设置已保存。连接状态会在上方自动更新。", "success");
  } catch {
    // Do not render or log API errors: they may contain sensitive data.
    message("#saved", "保存失败，设置未能保存。请重试。", "error");
  } finally {
    fields.disabled = false;
  }
});

$("#extension-id").textContent = chrome.runtime.id;
$("#copy-extension-id").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(chrome.runtime.id);
    message("#copy-status", "扩展 ID 已复制。", "success");
  } catch {
    message("#copy-status", "复制失败，请选择上方扩展 ID 手动复制。", "error");
  }
});
void loadConnection();

// These controls share presentation only. Each retains its own stored consent and Chrome permission.
function permissionControl({ key, permission, name, status, feedback, allow, revoke, all = false }) {
  let refreshVersion = 0;
  async function refresh() {
    const version = ++refreshVersion;
    try {
      const values = await chrome.storage.local.get(key);
      const granted = await chrome.permissions.contains(permission);
      if (version !== refreshVersion) return;
      const consent = values[key] === true;
      message(status, consent && granted ? (all ? "全站授权已开启" : "已授权")
        : consent ? "未授权 · Chrome 权限已撤销"
          : granted ? "未授权 · 尚未在此确认" : (all ? "全站授权未开启" : "未授权"),
      consent && granted ? "success" : "neutral");
      if (all) $("#all-websites-notice").hidden = !(consent && granted);
    } catch {
      if (version === refreshVersion) message(status, "无法读取授权状态，请重试", "error");
    }
  }
  const grantButton = $(allow), revokeButton = $(revoke);
  function busy(value) { grantButton.disabled = value; revokeButton.disabled = value; }
  grantButton.addEventListener("click", () => {
    busy(true);
    message(feedback, "等待浏览器授权确认…", "info");
    // Request directly in the real click gesture, before any await or storage access.
    void (async () => {
      try {
        const granted = await chrome.permissions.request(permission);
        if (!granted) {
          message(feedback, `未获得${name}权限，你可以再次点击授权。`, "warning");
          return;
        }
        await chrome.storage.local.set({ [key]: true });
        message(feedback, `${name}授权成功。`, "success");
      } catch {
        message(feedback, `${name}授权未完成，请重试。`, "error");
      } finally { await refresh(); busy(false); }
    })();
  });
  revokeButton.addEventListener("click", () => {
    busy(true);
    message(feedback, "正在撤销授权…", "info");
    void (async () => {
      let disabled = false;
      try {
        // Disable the application gate first, even if Chrome revocation subsequently fails.
        await chrome.storage.local.set({ [key]: false });
        disabled = true;
        if (!await chrome.permissions.remove(permission)) throw Error("Permission removal failed");
        message(feedback, all ? "已撤销全站授权。单站记录保留，需要时请重新点击授权。" : `已撤销${name}授权。`, "success");
      } catch {
        message(feedback, disabled ? `已停用${name}；Chrome 权限撤销未完成，请重试或检查扩展权限。`
          : "撤销失败，未能保存停用设置，请重试。", "error");
      } finally { await refresh(); busy(false); }
    })();
  });
  return refresh;
}

const refreshAll = permissionControl({ key: "allWebsites", permission: { origins: browser.allWebsiteOrigins },
  name: "全站", status: "#all-websites-status", feedback: "#all-websites-feedback",
  allow: "#allow-all-websites", revoke: "#revoke-all-websites", all: true });
const refreshGroups = permissionControl({ key: "groupAccess", permission: { permissions: ["tabGroups"] },
  name: "标签分组", status: "#tab-groups-status", feedback: "#tab-groups-feedback",
  allow: "#allow-tab-groups", revoke: "#revoke-tab-groups" });
const refreshBookmarks = permissionControl({ key: "bookmarkAccess", permission: { permissions: ["bookmarks"] },
  name: "书签", status: "#bookmarks-status", feedback: "#bookmarks-feedback",
  allow: "#allow-bookmarks", revoke: "#revoke-bookmarks" });

let websiteRefreshVersion = 0;
let websiteBusy = false;
async function showWebsites() {
  const version = ++websiteRefreshVersion;
  try {
    const { pageOrigins = [], allWebsites = false } = await chrome.storage.local.get(["pageOrigins", "allWebsites"]);
    const allGranted = allWebsites === true && await chrome.permissions.contains({ origins: browser.allWebsiteOrigins });
    const patterns = Array.isArray(pageOrigins) ? pageOrigins : [];
    const rows = await Promise.all(patterns.map(async (pattern) => {
      const granted = await chrome.permissions.contains({ origins: [pattern] });
      const item = document.createElement("li");
      const details = document.createElement("div");
      details.className = "website-details";
      const label = document.createElement("div");
      label.className = "website-pattern";
      label.textContent = pattern;
      const state = document.createElement("div");
      state.className = "website-state";
      state.textContent = allGranted ? "已授权 · 全站授权覆盖" : granted ? "已授权" : "未授权 · 请重新授权此网站";
      details.append(label, state);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button-small";
      remove.textContent = "撤销";
      remove.setAttribute("aria-label", `撤销 ${pattern} 的单站授权`);
      remove.disabled = websiteBusy;
      remove.addEventListener("click", () => { void revokeWebsite(pattern); });
      item.append(details, remove);
      return item;
    }));
    if (version !== websiteRefreshVersion) return;
    $("#authorized-websites").replaceChildren(...rows);
    $("#website-count").textContent = `${patterns.length} 个`;
    $("#websites-empty").hidden = patterns.length !== 0;
    message("#websites-error", "");
  } catch {
    if (version !== websiteRefreshVersion) return;
    $("#authorized-websites").replaceChildren();
    $("#website-count").textContent = "状态未知";
    $("#websites-empty").hidden = true;
    message("#websites-error", "无法读取网站授权列表，请重新打开页面后重试。", "error");
  }
}

function websiteButtons(busy) {
  websiteBusy = busy;
  $("#allow-website").disabled = busy;
  $("#authorized-websites").querySelectorAll("button").forEach((button) => { button.disabled = busy; });
}

async function revokeWebsite(pattern) {
  websiteButtons(true);
  let disabled = false;
  try {
    const current = await chrome.storage.local.get(["pageOrigins", "allWebsites"]);
    const patterns = Array.isArray(current.pageOrigins) ? current.pageOrigins : [];
    await chrome.storage.local.set({ pageOrigins: patterns.filter((value) => value !== pattern) });
    disabled = true;
    // Preserve the required loopback bridge permission and broad grants, as before.
    if (pattern !== "http://127.0.0.1/*" && current.allWebsites !== true) {
      if (!await chrome.permissions.remove({ origins: [pattern] })) throw Error("Permission removal failed");
    }
    message("#permission-status", current.allWebsites === true
      ? "单站记录已移除。全站授权开启时，该网站仍可访问。" : "已撤销该网站的单站授权。", "success");
  } catch {
    message("#permission-status", disabled ? "单站记录已移除；Chrome 权限撤销未完成，请在扩展管理页检查残留权限。"
      : "撤销失败，未能移除单站记录，请重试。", "error");
  } finally { await showWebsites(); websiteButtons(false); }
}

$("#website-permission").addEventListener("submit", (event) => {
  event.preventDefault();
  if (websiteBusy) return;
  let pattern;
  try { pattern = browser.pagePattern($("#website-url").value); } catch { pattern = null; }
  if (!pattern) { message("#permission-status", "仅支持不含账号密码的普通 HTTP/HTTPS 网站地址。", "error"); return; }
  websiteButtons(true);
  message("#permission-status", "等待浏览器授权确认…", "info");
  // Preserve the user gesture: do not await anything before request().
  void (async () => {
    try {
      if (!await chrome.permissions.request({ origins: [pattern] })) {
        message("#permission-status", "未获得网站权限，你可以再次点击授权。", "warning");
        return;
      }
      const { pageOrigins = [] } = await chrome.storage.local.get("pageOrigins");
      const patterns = Array.isArray(pageOrigins) ? pageOrigins : [];
      await chrome.storage.local.set({ pageOrigins: [...new Set([...patterns, pattern])] });
      message("#permission-status", "网站授权成功。", "success");
    } catch {
      message("#permission-status", "网站授权未完成，请重试。", "error");
    } finally { await showWebsites(); websiteButtons(false); }
  })();
});

function refreshPermissions() {
  void refreshAll();
  void showWebsites();
  void refreshGroups();
  void refreshBookmarks();
}
chrome.permissions.onAdded?.addListener(refreshPermissions);
chrome.permissions.onRemoved?.addListener(refreshPermissions);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.connectionStatus) {
    connectionState = changes.connectionStatus.newValue;
    showConnection();
  }
  if (area !== "local") return;
  if (changes.connection) showTokenState(changes.connection.newValue);
  if (["pageOrigins", "allWebsites", "groupAccess", "bookmarkAccess"].some((key) => key in changes)) refreshPermissions();
});
refreshPermissions();
