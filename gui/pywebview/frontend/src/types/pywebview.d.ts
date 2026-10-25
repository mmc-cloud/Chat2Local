export interface PingResult {
  ok: boolean;
  app: string;
  version: string;
  backend: string;
  platform?: string;
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
  management_listener?: ManagementListener;
}
export interface ManagementListener {
  host: string;
  port: number;
  source: "default" | "configured" | "cli" | "automatic";
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
export type ConfigValue = string | number | boolean | null | string[];
export type TokenPurpose = "browser" | "hub" | "agent";
export interface CredentialInfo {
  path: string;
  state: "ready" | "missing" | "invalid";
  reused?: boolean;
}
export interface BrowserStatus {
  state: "disabled" | "restart_required" | "listening" | "connected" | "failed" | "starting" | "core_stopped" | "configuration_error" | "unavailable";
}
export interface AutostartSnapshot {
  supported: boolean;
  enabled: boolean | null;
  backend: string;
  scope?: string;
  service?: string;
  path?: string;
  state: string;
}
export type ConfigMap = Record<string, Record<string, ConfigValue>>;
export interface ConfigSnapshot {
  persisted: ConfigMap;
  effective: ConfigMap;
  defaults: ConfigMap;
  restart_required?: boolean;
  management_listener?: ManagementListener;
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
  list_local_handoffs: () => Promise<Envelope<HandoffSnapshot>>;
  get_local_handoff: (handoff_id: string) => Promise<Envelope<HandoffDetail>>;
  save_local_handoff: (handoff_id: string, title: string, summary: string, content: string, expected_revision: number) => Promise<Envelope<HandoffDetail>>;
  browser_status: () => Promise<Envelope<BrowserStatus>>;
  credential_info: (purpose: TokenPurpose, path: string | null) => Promise<Envelope<CredentialInfo>>;
  manage_token: (purpose: TokenPurpose, path: string | null, value: string | null, regenerate: boolean, confirmed: boolean) => Promise<Envelope<CredentialInfo>>;
  copy_token: (purpose: TokenPurpose, path: string | null) => Promise<Envelope<{ copied: boolean }>>;
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
export interface HandoffProject {
  key: string;
  scope: "workspace" | "personal";
  name: string;
  path: string | null;
}
export interface LocalHandoff {
  id: string;
  project: HandoffProject;
  workstream: string;
  title: string;
  summary: string;
  revision: number;
  updated_at: string;
}
export interface HandoffDetail extends LocalHandoff {
  content: string;
}
export interface HandoffSnapshot {
  handoffs: LocalHandoff[];
  issues: { location: string; message: string }[];
}

declare global {
  interface Window {
    pywebview?: { api?: CoreApi };
  }
}
