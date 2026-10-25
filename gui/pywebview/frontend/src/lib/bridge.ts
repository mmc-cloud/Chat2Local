import { useEffect, useState } from "react";
import type {
  CoreApi,
  PingResult,
  Envelope,
  ConfigMap,
  LogCursor,
  DesktopChanges,
  TokenPurpose,
  AutostartSnapshot,
} from "@/types/pywebview";
export type BridgeState = {
  status: "connecting" | "failed" | "connected";
  info?: PingResult;
};
export class GuiError extends Error {}
// Desktop pywebview also uses HTTP internally, so protocol sniffing is unsafe.
// Vite marks only the packaged browser build with this explicit capability.
export const isWebUi = document.querySelector('meta[name="chat2local-transport"]')?.getAttribute("content") === "http";
export function errorMessage(error: unknown): string {
  return error instanceof GuiError ? error.message : "Backend unavailable";
}
let connection: Promise<PingResult> | undefined;
function connectPywebview(timeout = 8000): Promise<PingResult> {
  if (connection) return connection;
  connection = new Promise((resolve, reject) => {
    let pending = false;
    let settled = false;
    const cleanup = () => {
      clearTimeout(timer);
      window.removeEventListener("pywebviewready", ready);
    };
    const finish = (info?: PingResult) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (info) resolve(info);
      else reject(new GuiError("Backend unavailable"));
    };
    const ready = () => {
      const api = window.pywebview?.api;
      if (!api || pending) return;
      pending = true;
      Promise.resolve()
        .then(() => api.ping())
        .then((info) => {
          if (
            info.ok &&
            info.app === "Chat2Local" &&
            info.backend === "pywebview"
          )
            finish(info);
          else finish();
        })
        .catch(() => finish());
    };
    const timer = setTimeout(() => finish(), timeout);
    window.addEventListener("pywebviewready", ready);
    ready();
  });
  return connection;
}

export class PywebviewTransport {
  connect = connectPywebview;
  get api() { return window.pywebview?.api; }
}

export class HttpTransport {
  private connection?: Promise<PingResult>;
  private csrf = "";

  connect(timeout = 8000): Promise<PingResult> {
    this.connection ??= (async () => {
      try {
        const response = await fetch("/api/session", {
          mode: "same-origin", credentials: "same-origin", cache: "no-store",
          signal: AbortSignal.timeout(timeout),
        });
        const info = await response.json();
        if (!response.ok || !info.ok || info.app !== "Chat2Local" || info.backend !== "http" || typeof info.csrf !== "string")
          throw new GuiError("Backend unavailable");
        this.csrf = info.csrf;
        return info as PingResult;
      } catch {
        this.connection = undefined;
        throw new GuiError("Backend unavailable");
      }
    })();
    return this.connection;
  }

  async request<T>(method: string, parameters = {}): Promise<Envelope<T>> {
    await this.connect();
    try {
      const response = await fetch(`/api/${method}`, {
        method: "POST", mode: "same-origin", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Chat2Local-CSRF": this.csrf },
        body: JSON.stringify(parameters), signal: AbortSignal.timeout(30000),
      });
      const data = await response.json();
      if (typeof data.ok !== "boolean" || (!data.ok && typeof data.error?.message !== "string"))
        throw new GuiError("Backend unavailable");
      return data as Envelope<T>;
    } catch (error) {
      if (error instanceof GuiError) throw error;
      throw new GuiError("Backend unavailable");
    }
  }

  private unavailable = async (): Promise<never> => { throw new GuiError("Desktop operation unavailable in Web UI"); };
  api: CoreApi = {
    ping: () => this.connect(),
    runtime_status: () => this.request("runtime_status"),
    read_config: () => this.request("read_config"),
    validate_config: (candidate) => this.request("validate_config", { candidate }),
    save_config: (candidate) => this.request("save_config", { candidate }),
    read_logs: (cursor) => this.request("read_logs", { cursor }),
    list_devices: () => this.request("list_devices"),
    list_local_handoffs: () => this.request("list_local_handoffs"),
    get_local_handoff: (handoff_id) => this.request("get_local_handoff", { handoff_id }),
    save_local_handoff: (handoff_id, title, summary, content, expected_revision) =>
      this.request("save_local_handoff", { handoff_id, title, summary, content, expected_revision }),
    browser_status: () => this.request("browser_status"),
    credential_info: (purpose, path) => this.request("credential_info", { purpose, path }),
    manage_token: (purpose, path, value, regenerate, confirmed) => this.request("manage_token", { purpose, path, value, regenerate, confirmed }),
    copy_token: async (purpose, path) => {
      const result = await this.request<{ token: string }>("copy_token", { purpose, path });
      if (!result.ok) return result;
      try { await navigator.clipboard.writeText(result.result.token); }
      catch { throw new GuiError("Clipboard unavailable; try copying again"); }
      return { ok: true, result: { copied: true } };
    },
    stop_core: async () => {
      const result = await this.request<{ accepted: boolean }>("stop_core");
      return result.ok ? { ok: true, result: { lifecycle: "Stopped", core: null } } : result;
    },
    start_core: this.unavailable,
    restart_core: this.unavailable,
    choose_workspace: this.unavailable,
    read_desktop_preferences: this.unavailable,
    save_desktop_preferences: this.unavailable,
    take_desktop_navigation: async () => ({ ok: true, result: null }),
  };
}

const http = new HttpTransport();
const transport = isWebUi ? http : new PywebviewTransport();
export function connectBridge(timeout = 8000): Promise<PingResult> {
  return transport.connect(timeout);
}
async function invoke<T>(
  operation: (api: CoreApi) => Promise<Envelope<T>>,
): Promise<T> {
  await connectBridge();
  const api = transport.api;
  if (!api) throw new GuiError("Backend unavailable");
  const response = await operation(api);
  if (!response.ok) throw new GuiError(response.error.message);
  return response.result;
}
export const coreApi = {
  localHandoffs: () => invoke((api) => api.list_local_handoffs()),
  localHandoff: (id: string) => invoke((api) => api.get_local_handoff(id)),
  saveLocalHandoff: (id: string, title: string, summary: string, content: string, expectedRevision: number) =>
    invoke((api) => api.save_local_handoff(id, title, summary, content, expectedRevision)),
  browserStatus: () => invoke((api) => api.browser_status()),
  credentialInfo: (purpose: TokenPurpose, path: string | null) => invoke((api) => api.credential_info(purpose, path)),
  manageToken: (purpose: TokenPurpose, path: string | null, value: string | null = null, regenerate = false, confirmed = false) =>
    invoke((api) => api.manage_token(purpose, path, value, regenerate, confirmed)),
  copyToken: (purpose: TokenPurpose, path: string | null) => invoke((api) => api.copy_token(purpose, path)),
  status: () => invoke((api) => api.runtime_status()),
  start: (mode: string, workspace: string) =>
    invoke((api) => api.start_core(mode, workspace)),
  stop: () => invoke((api) => api.stop_core()),
  restart: () => invoke((api) => api.restart_core()),
  chooseWorkspace: () => invoke((api) => api.choose_workspace()),
  readConfig: () => invoke((api) => api.read_config()),
  validateConfig: (candidate: ConfigMap) =>
    invoke((api) => api.validate_config(candidate)),
  saveConfig: (candidate: ConfigMap) =>
    invoke((api) => api.save_config(candidate)),
  logs: (cursor: LogCursor | null) => invoke((api) => api.read_logs(cursor)),
  devices: () => invoke((api) => api.list_devices()),
};
export const desktopApi = {
  readPreferences: () => invoke((api) => api.read_desktop_preferences()),
  savePreferences: (changes: DesktopChanges) =>
    invoke((api) => api.save_desktop_preferences(changes)),
  takeNavigation: () => invoke((api) => api.take_desktop_navigation()),
};
export const autostartApi = {
  status: async () => {
    const response = await http.request<AutostartSnapshot>("autostart_status");
    if (!response.ok) throw new GuiError(response.error.message);
    return response.result;
  },
  setEnabled: async (enabled: boolean) => {
    const response = await http.request<AutostartSnapshot>("set_autostart", { enabled });
    if (!response.ok) throw new GuiError(response.error.message);
    return response.result;
  },
};
export function useBridge(): BridgeState {
  const [state, setState] = useState<BridgeState>({ status: "connecting" });
  useEffect(() => {
    let active = true;
    connectBridge()
      .then((info) => {
        if (active) setState({ status: "connected", info });
      })
      .catch(() => {
        if (active) setState({ status: "failed" });
      });
    return () => {
      active = false;
    };
  }, []);
  return state;
}
