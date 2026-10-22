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
