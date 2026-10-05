import { useCallback, useSyncExternalStore } from "react";

export type Language = "zh-CN" | "en";
const storageKey = "chat2local.gui.language";
const chinese: Record<string, string> = {
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

function readLanguage(): Language {
  try {
    return localStorage.getItem(storageKey) === "en" ? "en" : "zh-CN";
  } catch {
    return "zh-CN";
  }
}
let language = readLanguage();
document.documentElement.lang = language;
const listeners = new Set<() => void>();
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
function setLanguage(next: Language) {
  try {
    localStorage.setItem(storageKey, next);
  } catch {
    // The session still works when browser storage is unavailable.
  }
  language = next;
  document.documentElement.lang = next;
  listeners.forEach((listener) => listener());
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
