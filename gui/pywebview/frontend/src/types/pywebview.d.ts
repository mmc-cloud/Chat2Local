export interface PingResult {
  ok: boolean;
  app: string;
  version: string;
  backend: string;
}
export interface CoreStatus {
  instance_id: string;
  mode: "standalone" | "hub" | "agent";
  device_id: string;
  pid: number;
  workspace: string;
  started_at: string;
  version: string;
  state: string;
}
export interface RuntimeSnapshot {
  lifecycle:
    "Stopped" | "Starting" | "Running" | "Stopping" | "Stale" | "Error";
  core: CoreStatus | null;
  restart_required?: boolean;
  message?: string;
  desktop_error?: string | null;
}
export interface DesktopPreferences {
  schema_version: 1;
  language: "zh-CN" | "en";
  launch_at_login: boolean;
  silent_login_start: boolean;
  close_behavior: "tray" | "exit";
  auto_start_core: boolean;
  startup_mode: "standalone" | "hub" | "agent" | null;
  startup_workspace: string | null;
}
export type DesktopChanges = Partial<
  Pick<
    DesktopPreferences,
    | "language"
    | "launch_at_login"
    | "silent_login_start"
    | "close_behavior"
    | "auto_start_core"
  >
>;
export interface DesktopSnapshot {
  preferences: DesktopPreferences;
  launch_at_login_supported: boolean;
  tray_available: boolean;
}
export type ConfigValue = string | number | null | string[];
export type ConfigMap = Record<string, Record<string, ConfigValue>>;
export interface ConfigSnapshot {
  persisted: ConfigMap;
  effective: ConfigMap;
  defaults: ConfigMap;
  restart_required?: boolean;
}
export interface Device {
  device_id: string;
  kind: "local" | "remote";
  online: boolean;
  tools: string[];
}
export interface DeviceSnapshot {
  devices: Device[];
  mode: string | null;
}
export interface LogCursor {
  identity: string;
  offset: number;
  signature: string;
  prefix_size: number;
}
export interface LogBatch {
  lines: string[];
  cursor: LogCursor | null;
  reset: boolean;
  missing: boolean;
}
export type Envelope<T> =
  { ok: true; result: T } | { ok: false; error: { message: string } };
export interface CoreApi {
  read_desktop_preferences: () => Promise<Envelope<DesktopSnapshot>>;
  save_desktop_preferences: (
    changes: DesktopChanges,
  ) => Promise<Envelope<DesktopSnapshot>>;
  take_desktop_navigation: () => Promise<Envelope<"Logs" | null>>;
  ping: () => Promise<PingResult>;
  runtime_status: () => Promise<Envelope<RuntimeSnapshot>>;
  start_core: (
    mode: string,
    workspace: string,
  ) => Promise<Envelope<RuntimeSnapshot>>;
  stop_core: () => Promise<Envelope<RuntimeSnapshot>>;
  restart_core: () => Promise<Envelope<RuntimeSnapshot>>;
  choose_workspace: () => Promise<Envelope<string | null>>;
  read_config: () => Promise<Envelope<ConfigSnapshot>>;
  validate_config: (
    candidate: ConfigMap,
  ) => Promise<Envelope<{ valid: boolean }>>;
  save_config: (candidate: ConfigMap) => Promise<Envelope<ConfigSnapshot>>;
  read_logs: (cursor: LogCursor | null) => Promise<Envelope<LogBatch>>;
  list_devices: () => Promise<Envelope<DeviceSnapshot>>;
}
declare global {
  interface Window {
    pywebview?: { api?: CoreApi };
  }
}
