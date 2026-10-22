import { useCallback, useSyncExternalStore } from "react";
import { desktopApi } from "@/lib/bridge";

export type Language = "zh-CN" | "en";
const legacyStorageKey = "chat2local.gui.language";
const cacheStorageKey = "chat2local.gui.language.cache";
const chinese: Record<string, string> = {
  Browser: "浏览器",
  "Loading browser state...": "正在读取 Browser 状态…",
  "Desktop window unavailable": "桌面窗口不可用",
  "Invalid credential action": "凭据操作参数无效",
  "These actions manage token files. CLI and CHAT2LOCAL_HUB_TOKEN overrides still take precedence.": "这些操作管理 Token 文件。CLI 和 CHAT2LOCAL_HUB_TOKEN 的覆盖优先级保持不变。",
  "Optional Chrome connection. Available in every Core mode.": "可选的 Chrome 连接，可在所有 Core 模式下配置。",
  "Enable Browser Bridge": "启用 Browser Bridge",
  "Chrome extension ID": "Chrome 扩展 ID",
  "Local Browser port": "本机浏览器端口",
  "Token management": "Token 管理",
  "Import the token supplied by your Hub. Agent never generates a replacement shared token.": "导入 Hub 提供的 Token。Agent 不会生成新的共享 Token 来替代它。",
  "Hub and Agents share this token. Existing credential files are kept.": "Hub 与 Agent 使用同一个 Token。已有凭据文件会被保留。",
  "Browser uses an independent token. Copy it into the Chrome extension options after saving.": "浏览器使用独立 Token。保存配置后，将其复制到 Chrome 扩展选项页完成配对。",
  "Token file: ready": "Token 文件：已就绪",
  "Token file: missing": "Token 文件：尚未创建",
  "Token file: invalid": "Token 文件：无效或不可读取",
  "Hub token to import": "要导入的 Hub Token",
  "Paste the existing Hub token": "粘贴已有的 Hub Token",
  "Generate or reuse token": "生成或复用 Token",
  "Import Hub token": "导入 Hub Token",
  "Use existing token file": "使用已有 Token 文件",
  "Copy token": "复制 Token",
  "Regenerate token": "重新生成 Token",
  "Confirm regeneration": "确认重新生成",
  Cancel: "取消",
  "Token copied to clipboard": "Token 已复制到剪贴板",
  "Token file ready. Save changes to apply its path.": "Token 文件已就绪，请保存配置以应用其路径。",
  "Token regenerated. Restart Core and pair the Chrome extension again.": "Token 已重新生成。请重启 Core，并重新配对 Chrome 扩展。",
  "Regenerating invalidates the previous Browser token. Restart Core and pair the Chrome extension again. Continue?": "重新生成将使旧 Browser Token 失效，需要重启 Core 并重新配对 Chrome 扩展。是否继续？",
  "Browser is disabled": "Browser 未启用",
  "Browser configuration saved. Restart Core to apply changes.": "Browser 配置已保存，等待重启 Core 后生效。",
  "Bridge is listening. Chrome extension is not connected.": "Bridge 已启动，Chrome 扩展尚未连接。",
  "Chrome extension is connected": "Chrome 扩展已连接",
  "Bridge failed to start. Check the token file and local port.": "Bridge 启动失败，请检查 Token 文件和本机端口。",
  "Browser Bridge is starting": "Browser Bridge 正在启动",
  "Browser is configured. Start Core to connect.": "Browser 已配置，请启动 Core 后连接。",
  "Browser configuration is invalid": "Browser 配置无效",
  "Browser state unavailable. Restart an updated Core to enable status reporting.": "无法读取 Browser 状态，更新 Core 并重启后可显示实时状态。",
  "Invalid credential purpose or path": "凭据用途或路径无效",
  "Credential path must be absolute and cannot use links": "凭据路径必须是绝对路径，不能使用符号链接或目录联接。",
  "Invalid token": "Token 格式无效",
  "Credential file is unavailable or invalid": "凭据文件不可读取或内容无效",
  "Browser token must be independent of Hub and Agent tokens": "Browser Token 必须独立于 Hub 和 Agent 的共享 Token。",
  "New or regenerated credentials must be inside the private user data directory": "新建或重新生成的凭据必须位于私有用户数据目录内。",
  "Credentials cannot be written inside a Git repository": "不能将凭据写入 Git 仓库内。",
  "Browser token regeneration requires confirmation": "重新生成 Browser Token 需要再次确认。",
  "Agent requires an existing Hub token; generation is not supported": "Agent 需要已有的 Hub Token，不能自行生成替代凭据。",
  "Credential file already exists with a different token; choose another path": "该文件已有不同的 Token，请选择其他路径；原文件不会被覆盖。",
  "Credential file was created concurrently; retry to reuse it": "另一个操作刚创建了该凭据文件，请重试以复用它。",
  "Could not save credential file; existing credentials were preserved": "无法保存凭据文件，已有凭据已保留。",
  "Native token copy is only available on Windows": "原生 Token 复制当前仅支持 Windows。",
  "Clipboard unavailable; try copying again": "剪贴板暂不可用，请重试复制。",
  Overview: "概览",
  Devices: "设备",
  Settings: "设置",
  Logs: "日志",
  Workspace: "工作区",
  WORKSPACE: "工作区",
  "Main navigation": "主导航",
  "Local control": "本地管理",
  "Local by design.": "本地运行。",
  "Connected when you need it.": "按需连接。",
  "Backend connected": "已连接本地后端",
  "Backend unavailable": "本地后端不可用",
  "Connecting...": "正在连接…",
  "Live connection to the local Python shell.":
    "与本地 Python 窗口后端的实时连接。",
  "GUI Stage 2": "GUI 阶段 2",
  "YOUR LOCAL CONTROL PLANE": "本地管理",
  "Your local agent, workspace and connection at a glance.":
    "查看本地节点、工作区和连接状态。",
  "See Logs for diagnostics.": "可在日志页面查看诊断信息。",
  "Restart required. Saved configuration will apply when Core restarts.":
    "需要重启 Core，已保存的配置将在重启后生效。",
  "Core status": "Core 运行状态",
  "No active Core": "Core 未运行",
  "Choose a mode and workspace to get started.":
    "选择运行模式和工作区后即可启动。",
  Device: "设备",
  "Process ID": "进程 ID",
  "Core version": "Core 版本",
  Started: "启动时间",
  "Core state": "Core 状态",
  Stop: "停止",
  Restart: "重启",
  "Starting...": "正在启动…",
  "Start Core": "启动 Core",
  "Startup context": "启动设置",
  Mode: "运行模式",
  standalone: "独立模式",
  hub: "Hub 中枢",
  agent: "Agent 节点",
  Standalone: "独立模式",
  Hub: "Hub 中枢",
  Agent: "Agent 节点",
  "Choose a workspace folder": "选择工作区文件夹",
  Browse: "浏览",
  "Stop Core before switching mode or workspace.":
    "切换运行模式或工作区前，请先停止 Core。",
  "Used for the next Core start. Configuration comes from config.yaml.":
    "用于下一次启动 Core，配置读取自 config.yaml。",
  "ACTIVE WORKSPACE": "当前工作区",
  "NEXT STARTUP WORKSPACE": "下次启动的工作区",
  "No workspace selected": "尚未选择工作区",
  "Core validates access using the configured allowed roots.":
    "Core 根据配置的允许根目录检查访问权限。",
  "Connected to the local Python shell. Core continues running when this window closes.":
    "已连接本地窗口后端。关闭此窗口后，Core 仍会继续运行。",
  "Waiting for the desktop Python bridge.": "正在等待桌面窗口后端连接。",
  Running: "运行中",
  Stopped: "已停止",
  Starting: "启动中",
  Stopping: "停止中",
  Stale: "运行信息已失效",
  Error: "错误",
  Connecting: "连接中",
  running: "运行中",
  starting: "启动中",
  stopped: "已停止",
  stopping: "停止中",
  connecting: "连接中",
  connected: "已连接",
  reconnecting: "重连中",
  offline: "离线",
  configuration_error: "配置错误",
  authentication_failed: "身份验证失败",
  device_already_connected: "设备已被其他连接占用",
  device_id_conflict: "设备 ID 与 Hub 冲突",
  protocol_incompatible: "与 Hub 的协议不兼容",
  registration_rejected: "设备注册被拒绝",
  "PERSISTED CONFIGURATION": "配置与偏好",
  "Explicit values are saved. Unset fields use Core defaults.":
    "仅保存明确设置的值，未设置的字段使用 Core 默认值。",
  Reload: "重新加载",
  "Configuration is invalid or unavailable": "配置无效或不可用",
  "Loading configuration...": "正在加载配置…",
  Advanced: "高级设置",
  Security: "安全",
  Process: "进程",
  Read: "文件读取",
  Search: "搜索",
  Auth: "身份验证",
  "Bounded file previews.": "文件预览的读取上限。",
  "Search limits and timeout.": "搜索结果上限与超时。",
  "Execution, output and cleanup.": "命令执行、输出缓冲与进程清理。",
  "Workspace access boundaries.": "工作区访问边界。",
  "Identity and listener configuration.": "设备标识与监听配置。",
  "Outbound Hub connection.": "连接远端 Hub。",
  "Optional OAuth authentication.": "可选的 OAuth 身份验证。",
  "Maximum lines": "最大行数",
  "Maximum bytes": "最大字节数",
  "Maximum results": "最大结果数",
  "Timeout (seconds)": "超时（秒）",
  Shell: "命令解释器",
  "foreground timeout": "前台等待超时（秒）",
  "stdout buffer limit": "标准输出缓冲上限（字节）",
  "stderr buffer limit": "标准错误缓冲上限（字节）",
  "response output limit": "响应输出上限（字节）",
  "terminate grace period": "终止宽限期（秒）",
  "finished retention": "已结束进程保留时间（秒）",
  "Allowed roots": "允许的根目录",
  "Allowed root {index}": "允许的根目录 {index}",
  "Remove root {index}": "移除根目录 {index}",
  "Add root": "添加根目录",
  "Device ID": "设备 ID",
  Host: "监听地址",
  Port: "监听端口",
  "Token file path": "令牌文件路径",
  "Hub URL": "Hub 地址",
  Proxy: "代理",
  "Authentication mode": "身份验证方式",
  Provider: "身份验证提供方",
  "Issuer URL": "签发方地址",
  "Resource server URL": "资源服务器地址",
  "System proxy": "系统代理",
  "Direct connection": "直接连接",
  "Custom URL": "自定义地址",
  "Custom proxy URL": "自定义代理地址",
  "HTTP / HTTPS / SOCKS proxy URL": "HTTP / HTTPS / SOCKS 代理地址",
  "Custom proxy URL is hidden. Edit or paste a new value to replace it.":
    "自定义代理地址始终隐藏，可编辑或粘贴新值替换。",
  "Not configured (null)": "未配置（null）",
  "Not configured": "未配置",
  "Explicit null": "已明确设为 null",
  "Explicit value": "已明确设置",
  "Core default": "Core 默认值",
  "Set null": "设为 null",
  Reset: "重置",
  "Unsaved Changes": "有未保存的更改",
  "Only explicit fields are persisted.": "仅保存明确设置的字段。",
  "Saving...": "正在保存…",
  "Save changes": "保存更改",
  "Configuration saved": "配置已保存",
  "Configuration saved · Restart required": "配置已保存 · 需要重启 Core",
  "Display language": "显示语言",
  Desktop: "桌面",
  "Saved immediately. Separate from Core configuration.":
    "修改立即保存，独立于 Core 配置。",
  "Launch Chat2Local when I sign in to Windows":
    "登录 Windows 后启动 Chat2Local",
  "Start silently when launched at sign-in": "登录启动时静默运行",
  "Start Core automatically": "自动启动 Core",
  "Close button": "点击关闭按钮",
  "Hide to tray": "隐藏到托盘",
  "Exit Chat2Local": "退出 Chat2Local",
  "Loading desktop preferences...": "正在加载桌面设置…",
  "Tray unavailable; closing the window will exit Chat2Local":
    "托盘不可用，关闭窗口将退出 Chat2Local",
  "Desktop operation failed; see desktop log": "桌面操作失败，请查看桌面日志",
  "Could not save Core startup context; see desktop log":
    "无法保存 Core 启动设置，请查看桌面日志",
  "Could not save desktop preferences; see desktop log":
    "无法保存桌面设置，请查看桌面日志",
  "Could not update Windows login startup; see desktop log":
    "无法修改 Windows 登录启动设置，请查看桌面日志",
  "Desktop preferences invalid or unreadable; Windows login startup was not changed":
    "桌面设置文件无效或无法读取，Windows 登录启动项未做修改",
  "Windows login startup rollback failed; see desktop log":
    "Windows 登录启动设置回滚失败，请查看桌面日志",
  "Windows login startup is not supported on this platform":
    "当前平台不支持 Windows 登录启动",
  "Invalid desktop preferences fields": "无效的桌面设置字段",
  "Invalid desktop preference: launch_at_login": "无效的登录启动设置",
  "Invalid desktop preference: silent_login_start": "无效的静默登录启动设置",
  "Invalid desktop preference: auto_start_core": "无效的自动启动 Core 设置",
  "Invalid desktop preference: close_behavior": "无效的关闭按钮设置",
  "Applies immediately and is remembered for this GUI. Core configuration is unchanged.":
    "立即生效并记住选择，不影响 Core 配置。",
  auto: "自动检测",
  none: "不启用",
  "ACTIVITY STREAM": "运行记录",
  "Read-only view of the local Core log.": "本地 Core 日志的只读视图。",
  "Search logs": "搜索日志",
  "Search logs...": "搜索日志…",
  "Log level": "日志级别",
  "All levels": "全部级别",
  DEBUG: "调试",
  INFO: "信息",
  WARNING: "警告",
  ERROR: "错误",
  CRITICAL: "严重错误",
  Follow: "自动跟随",
  "Core log output": "Core 日志输出",
  "No log file yet. Logs appear when Core starts.":
    "尚无日志文件，启动 Core 后将生成日志。",
  "No logs match your filters.": "没有符合筛选条件的日志。",
  "Waiting for log entries...": "正在等待日志…",
  "{shown} of {total} lines": "显示 {shown} / {total} 行",
  Following: "跟随中",
  Paused: "已暂停",
  "DEVICE NETWORK": "设备网络",
  "Devices currently known to your local Core.":
    "本地 Hub 和当前在线的 Agent 节点。",
  "{count} connected devices": "{count} 台已连接设备",
  "This device": "本机",
  local: "本地",
  remote: "远端",
  "{count} capabilities": "{count} 项能力",
  Online: "在线",
  Offline: "离线",
  "No active Core. Start Core from Overview to see devices.":
    "Core 尚未运行，请从概览页面启动 Hub 后查看设备。",
  "Loading device snapshot...": "正在加载设备信息…",
  "Agent mode shows this device. The Hub network is managed by the Hub.":
    "Agent 模式显示本机，设备网络由 Hub 管理。",
  Notifications: "通知",
  "Close toast": "关闭通知",
  "Invalid GUI request": "无效的 GUI 请求",
  "Internal GUI/Core error; see logs": "GUI/Core 内部错误，请查看日志",
};

function readStoredLanguage(key: string): Language | null {
  try {
    const value = localStorage.getItem(key);
    return value === "en" || value === "zh-CN" ? value : null;
  } catch {
    return null;
  }
}

function cacheLanguage(next: Language) {
  try {
    localStorage.setItem(cacheStorageKey, next);
  } catch {
    // Cache is optional; Desktop Preferences remain authoritative.
  }
}

function applyLanguage(next: Language) {
  if (language === next) return;
  language = next;
  document.documentElement.lang = next;
  listeners.forEach((listener) => listener());
}

let language =
  readStoredLanguage(cacheStorageKey) ??
  readStoredLanguage(legacyStorageKey) ??
  "zh-CN";
document.documentElement.lang = language;
const listeners = new Set<() => void>();
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export async function initializeLanguage() {
  try {
    const snapshot = await desktopApi.readPreferences();
    const legacy = readStoredLanguage(legacyStorageKey);
    if (legacy) {
      const saved = await desktopApi.savePreferences({ language: legacy });
      applyLanguage(saved.preferences.language);
      cacheLanguage(saved.preferences.language);
      try {
        localStorage.removeItem(legacyStorageKey);
      } catch {
        // Retry migration on a future launch if legacy storage cannot be cleared.
      }
      return;
    }
    applyLanguage(snapshot.preferences.language);
    cacheLanguage(snapshot.preferences.language);
  } catch {
    // Keep the cached/default language while the Python bridge is unavailable.
  }
}

async function setLanguage(next: Language) {
  const previous = language;
  applyLanguage(next);
  try {
    const saved = await desktopApi.savePreferences({ language: next });
    applyLanguage(saved.preferences.language);
    cacheLanguage(saved.preferences.language);
  } catch (error) {
    applyLanguage(previous);
    throw error;
  }
}
export function useLocale() {
  const current = useSyncExternalStore(subscribe, () => language);
  const t = useCallback(
    (message: string, values: Record<string, string | number> = {}) => {
      const text =
        current === "zh-CN" && Object.hasOwn(chinese, message)
          ? chinese[message]
          : message;
      return text.replace(/\{(\w+)\}/g, (placeholder, key: string) =>
        Object.hasOwn(values, key) ? String(values[key]) : placeholder,
      );
    },
    [current],
  );
  return { language: current, setLanguage, t };
}
