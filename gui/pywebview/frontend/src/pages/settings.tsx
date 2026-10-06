import { useEffect, useState } from "react";
import { Save, RotateCcw, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DesktopSettings } from "@/components/desktop-settings";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { coreApi, errorMessage } from "@/lib/bridge";
import { useLocale } from "@/lib/locale";
import type { ConfigMap, ConfigSnapshot, ConfigValue } from "@/types/pywebview";
interface Field {
  name: string;
  label: string;
  kind: "text" | "number" | "select" | "roots" | "proxy";
  nullable?: boolean;
  options?: string[];
}
interface Section {
  name: string;
  description: string;
  fields: Field[];
}
// UI labels and controls, not validation rules. Core owns the schema and defaults.
const sections: Section[] = [
  {
    name: "read",
    description: "Bounded file previews.",
    fields: [
      { name: "max_lines", label: "Maximum lines", kind: "number" },
      { name: "max_bytes", label: "Maximum bytes", kind: "number" },
    ],
  },
  {
    name: "search",
    description: "Search limits and timeout.",
    fields: [
      { name: "max_results", label: "Maximum results", kind: "number" },
      { name: "timeout", label: "Timeout (seconds)", kind: "number" },
    ],
  },
  {
    name: "process",
    description: "Execution, output and cleanup.",
    fields: [
      {
        name: "shell",
        label: "Shell",
        kind: "select",
        options: [
          "auto",
          "pwsh",
          "powershell",
          "powershell.exe",
          "cmd",
          "cmd.exe",
          "bash",
          "sh",
          "zsh",
        ],
      },
      ...[
        "foreground_timeout",
        "stdout_buffer_limit",
        "stderr_buffer_limit",
        "response_output_limit",
        "terminate_grace_period",
        "finished_retention",
      ].map((name) => ({
        name,
        label: name.replaceAll("_", " "),
        kind: "number" as const,
      })),
    ],
  },
  {
    name: "security",
    description: "Workspace access boundaries.",
    fields: [{ name: "allowed_roots", label: "Allowed roots", kind: "roots" }],
  },
  {
    name: "hub",
    description: "Identity and listener configuration.",
    fields: [
      { name: "device_id", label: "Device ID", kind: "text", nullable: true },
      { name: "host", label: "Host", kind: "text", nullable: true },
      { name: "port", label: "Port", kind: "number", nullable: true },
      {
        name: "token_file",
        label: "Token file path",
        kind: "text",
        nullable: true,
      },
    ],
  },
  {
    name: "agent",
    description: "Outbound Hub connection.",
    fields: [
      { name: "device_id", label: "Device ID", kind: "text", nullable: true },
      { name: "hub_url", label: "Hub URL", kind: "text", nullable: true },
      {
        name: "token_file",
        label: "Token file path",
        kind: "text",
        nullable: true,
      },
      { name: "proxy", label: "Proxy", kind: "proxy" },
    ],
  },
  {
    name: "auth",
    description: "Optional OAuth authentication.",
    fields: [
      {
        name: "mode",
        label: "Authentication mode",
        kind: "select",
        options: ["none", "oauth"],
      },
      {
        name: "provider",
        label: "Provider",
        kind: "select",
        options: ["workos"],
        nullable: true,
      },
      { name: "issuer_url", label: "Issuer URL", kind: "text", nullable: true },
      {
        name: "resource_server_url",
        label: "Resource server URL",
        kind: "text",
        nullable: true,
      },
    ],
  },
];
function canonical(config: ConfigMap) {
  return JSON.stringify(
    Object.fromEntries(
      Object.keys(config)
        .sort()
        .map((section) => [
          section,
          Object.fromEntries(
            Object.entries(config[section]).sort(([left], [right]) =>
              left.localeCompare(right),
            ),
          ),
        ]),
    ),
  );
}
const advancedSections = ["security", "process", "read", "search"].flatMap(
  (name) => sections.filter((section) => section.name === name),
);
export function Settings({ currentMode }: { currentMode: string }) {
  const { t, language, setLanguage } = useLocale();
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null);
  const [candidate, setCandidate] = useState<ConfigMap>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    coreApi
      .readConfig()
      .then((data) => {
        if (active) {
          setSnapshot(data);
          setCandidate(structuredClone(data.persisted));
        }
      })
      .catch((error) => {
        if (active) setError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, []);
  const dirty = snapshot
    ? canonical(candidate) !== canonical(snapshot.persisted)
    : false;
  const valueFor = (section: string, name: string) =>
    Object.hasOwn(candidate[section] ?? {}, name)
      ? candidate[section][name]
      : (snapshot?.defaults[section]?.[name] ?? null);
  const update = (section: string, name: string, value: ConfigValue) =>
    setCandidate((current) => ({
      ...current,
      [section]: { ...current[section], [name]: value },
    }));
  const reset = (section: string, name: string) =>
    setCandidate((current) => {
      const next = structuredClone(current);
      if (next[section]) {
        delete next[section][name];
        if (!Object.keys(next[section]).length) delete next[section];
      }
      return next;
    });
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await coreApi.validateConfig(candidate);
      const saved = await coreApi.saveConfig(candidate);
      setSnapshot(saved);
      setCandidate(structuredClone(saved.persisted));
      toast.success(
        t(
          saved.restart_required
            ? "Configuration saved · Restart required"
            : "Configuration saved",
        ),
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const reload = async () => {
    setBusy(true);
    setError("");
    try {
      const data = await coreApi.readConfig();
      setSnapshot(data);
      setCandidate(structuredClone(data.persisted));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const renderSection = (section: Section) => (
    <section className="settings-section" key={section.name}>
      <div className="settings-intro">
        <h2>{t(section.name[0].toUpperCase() + section.name.slice(1))}</h2>
        <p>{t(section.description)}</p>
      </div>
      <div className="settings-fields">
        {section.fields
          .filter(
            (field) =>
              section.name !== "auth" ||
              field.name === "mode" ||
              valueFor("auth", "mode") === "oauth",
          )
          .map((field) => {
            const value = valueFor(section.name, field.name);
            const explicit = Object.hasOwn(
              candidate[section.name] ?? {},
              field.name,
            );
            const id = `${section.name}-${field.name}`;
            const set = (value: ConfigValue) =>
              update(section.name, field.name, value);
            const text =
              typeof value === "string" || typeof value === "number"
                ? String(value)
                : "";
            return (
              <div
                className={
                  field.kind === "roots" || field.kind === "proxy"
                    ? "config-field full-field"
                    : "config-field"
                }
                key={field.name}
              >
                <label htmlFor={id}>{t(field.label)}</label>
                {field.kind === "roots" ? (
                  <div className="roots-editor">
                    {(Array.isArray(value) ? value : []).map((path, index) => (
                      <div className="path-picker" key={index}>
                        <Input
                          aria-label={t("Allowed root {index}", {
                            index: index + 1,
                          })}
                          value={path}
                          onChange={(event) => {
                            const roots = [
                              ...(Array.isArray(value) ? value : []),
                            ];
                            roots[index] = event.target.value;
                            set(roots);
                          }}
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          type="button"
                          aria-label={t("Remove root {index}", {
                            index: index + 1,
                          })}
                          onClick={() =>
                            set(
                              (Array.isArray(value) ? value : []).filter(
                                (_, position) => position !== index,
                              ),
                            )
                          }
                        >
                          <X />
                        </Button>
                      </div>
                    ))}
                    <Button
                      variant="outline"
                      size="sm"
                      type="button"
                      onClick={() =>
                        set([...(Array.isArray(value) ? value : []), ""])
                      }
                    >
                      <Plus />
                      {t("Add root")}
                    </Button>
                  </div>
                ) : field.kind === "proxy" ? (
                  <>
                    <Select
                      value={
                        text === "system" || text === "direct" ? text : "custom"
                      }
                      onValueChange={(selected) =>
                        set(selected === "custom" ? "" : selected)
                      }
                    >
                      <SelectTrigger id={id}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="system">
                          {t("System proxy")}
                        </SelectItem>
                        <SelectItem value="direct">
                          {t("Direct connection")}
                        </SelectItem>
                        <SelectItem value="custom">
                          {t("Custom URL")}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                    {text !== "system" && text !== "direct" && (
                      <>
                        <Input
                          aria-label={t("Custom proxy URL")}
                          type="password"
                          autoComplete="off"
                          value={text}
                          onChange={(event) => set(event.target.value)}
                          placeholder={t("HTTP / HTTPS / SOCKS proxy URL")}
                        />
                        <p className="text-muted-foreground text-xs">
                          {t(
                            "Custom proxy URL is hidden. Edit or paste a new value to replace it.",
                          )}
                        </p>
                      </>
                    )}
                  </>
                ) : field.kind === "select" ? (
                  <Select
                    value={text || "__null__"}
                    onValueChange={(selected) =>
                      set(selected === "__null__" ? null : selected)
                    }
                  >
                    <SelectTrigger id={id}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {field.nullable && (
                        <SelectItem value="__null__">
                          {t("Not configured (null)")}
                        </SelectItem>
                      )}
                      {field.options?.map((option) => (
                        <SelectItem key={option} value={option}>
                          {t(option)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    id={id}
                    type={field.kind === "number" ? "number" : "text"}
                    step="any"
                    value={text}
                    placeholder={
                      value === null ? t("Not configured") : undefined
                    }
                    onChange={(event) =>
                      set(
                        field.kind === "number" && event.target.value !== ""
                          ? Number(event.target.value)
                          : event.target.value,
                      )
                    }
                  />
                )}
                <div className="field-meta">
                  <span>
                    {t(
                      explicit
                        ? value === null
                          ? "Explicit null"
                          : "Explicit value"
                        : "Core default",
                    )}
                  </span>
                  <div>
                    {field.nullable && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => set(null)}
                      >
                        {t("Set null")}
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!explicit}
                      onClick={() => reset(section.name, field.name)}
                    >
                      {t("Reset")}
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
      </div>
    </section>
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{t("PERSISTED CONFIGURATION")}</p>
          <h1>{t("Settings")}</h1>
          <p>
            {t("Explicit values are saved. Unset fields use Core defaults.")}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void reload()}
          disabled={busy || dirty}
        >
          <RotateCcw />
          {t("Reload")}
        </Button>
      </div>
      <section className="settings-section">
        <div className="settings-intro">
          <h2>{t("Display language")}</h2>
          <p>
            {t(
              "Applies immediately and is remembered for this GUI. Core configuration is unchanged.",
            )}
          </p>
        </div>
        <div className="settings-fields">
          <Select
            value={language}
            onValueChange={(value) => {
              void setLanguage(value === "en" ? "en" : "zh-CN").catch((error) =>
                toast.error(t(errorMessage(error))),
              );
            }}
          >
            <SelectTrigger
              id="display-language"
              aria-label={t("Display language")}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="zh-CN">简体中文</SelectItem>
              <SelectItem value="en">English</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </section>
      <DesktopSettings />
      {error && (
        <div className="error-note" role="alert">
          {t("Configuration is invalid or unavailable")}: {t(error)}
        </div>
      )}
      {!snapshot && !error && <p>{t("Loading configuration...")}</p>}
      {snapshot && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <fieldset disabled={busy} className="config-fieldset">
            {sections
              .filter(
                (section) =>
                  section.name === currentMode ||
                  (currentMode === "hub" && section.name === "auth"),
              )
              .map(renderSection)}
            <details className="settings-advanced">
              <summary>{t("Advanced")}</summary>
              {advancedSections.map(renderSection)}
            </details>
            <div className="save-bar">
              <span role="status">
                {dirty ? (
                  <>
                    <i className="status-dot unsaved" />
                    {t("Unsaved Changes")}
                  </>
                ) : (
                  t("Only explicit fields are persisted.")
                )}
              </span>
              <Button type="submit" size="sm" disabled={busy || !dirty}>
                <Save />
                {t(busy ? "Saving..." : "Save changes")}
              </Button>
            </div>
          </fieldset>
        </form>
      )}
    </>
  );
}
