import { useEffect, useState } from "react";
import type {
  CoreApi,
  PingResult,
  Envelope,
  ConfigMap,
  LogCursor,
  DesktopChanges,
} from "@/types/pywebview";
export type BridgeState = {
  status: "connecting" | "failed" | "connected";
  info?: PingResult;
};
export class GuiError extends Error {}
export function errorMessage(error: unknown): string {
  return error instanceof GuiError ? error.message : "Backend unavailable";
}
let connection: Promise<PingResult> | undefined;
export function connectBridge(timeout = 8000): Promise<PingResult> {
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
async function invoke<T>(
  operation: (api: CoreApi) => Promise<Envelope<T>>,
): Promise<T> {
  await connectBridge();
  const api = window.pywebview?.api;
  if (!api) throw new GuiError("Backend unavailable");
  const response = await operation(api);
  if (!response.ok) throw new GuiError(response.error.message);
  return response.result;
}
export const coreApi = {
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
