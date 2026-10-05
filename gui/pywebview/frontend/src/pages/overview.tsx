import { Activity, Folder, Play, RotateCw, Square, Cable } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatusBadge } from "@/components/status-badge";
import { coreApi, errorMessage } from "@/lib/bridge";
import { useLocale } from "@/lib/locale";
import type { RuntimeSnapshot } from "@/types/pywebview";
interface Props {
  runtime: RuntimeSnapshot | null;
  error: string;
  mode: string;
  workspace: string;
  onMode: (mode: string) => void;
  onWorkspace: (workspace: string) => void;
  busy: string;
  onAction: (action: "start" | "stop" | "restart") => Promise<void>;
  connected: boolean;
}
export function Overview({
  runtime,
  error,
  mode,
  workspace,
  onMode,
  onWorkspace,
  busy,
  onAction,
  connected,
}: Props) {
  const { t, language } = useLocale();
  const core = runtime?.core;
  const lifecycle = busy
    ? busy === "start"
      ? "Starting"
      : "Stopping"
    : (runtime?.lifecycle ?? "Connecting");
  const running = Boolean(core);
  const locked =
    running ||
    Boolean(busy) ||
    runtime?.lifecycle === "Starting" ||
    runtime?.lifecycle === "Stopping";
  const perform = (action: "start" | "stop" | "restart") =>
    void onAction(action).catch((error) => toast.error(t(errorMessage(error))));
  const browse = async () => {
    try {
      const selected = await coreApi.chooseWorkspace();
      if (selected) onWorkspace(selected);
    } catch (error) {
      toast.error(t(errorMessage(error)));
    }
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{t("YOUR LOCAL CONTROL PLANE")}</p>
          <h1>Chat2Local</h1>
          <p>{t("Your local agent, workspace and connection at a glance.")}</p>
        </div>
        <StatusBadge online={running} label={t(lifecycle)} />
      </div>
      {(error || runtime?.message) && (
        <div className="error-note" role="alert">
          {t(error || runtime?.message || "")} ·{" "}
          {t("See Logs for diagnostics.")}
        </div>
      )}
      {runtime?.restart_required && (
        <div className="info-note">
          <RotateCw size={17} />
          <p>
            {t(
              "Restart required. Saved configuration will apply when Core restarts.",
            )}
          </p>
        </div>
      )}
      <div className="overview-grid">
        <Card>
          <CardHeader>
            <div className="section-title">
              <CardTitle>
                <Activity size={17} />
                {t("Core status")}
              </CardTitle>
              <Badge variant="secondary">
                {t(core?.mode ?? "No active Core")}
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <div className="runtime-summary">
              <div>
                <StatusBadge online={running} label={t(lifecycle)} />
                <p>
                  {t(
                    core?.state ??
                      "Choose a mode and workspace to get started.",
                  )}
                </p>
              </div>
            </div>
            <dl className="runtime-details">
              {[
                ["Device", core?.device_id],
                ["Process ID", core?.pid],
                ["Core version", core?.version],
                [
                  "Started",
                  core?.started_at
                    ? new Date(core.started_at).toLocaleString(language)
                    : undefined,
                ],
                ["Core state", core?.state ? t(core.state) : undefined],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt>{t(String(label))}</dt>
                  <dd>{value ?? "—"}</dd>
                </div>
              ))}
            </dl>
            <div className="actions">
              {running ? (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={Boolean(busy)}
                    onClick={() => perform("stop")}
                  >
                    <Square />
                    {t("Stop")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={Boolean(busy)}
                    onClick={() => perform("restart")}
                  >
                    <RotateCw />
                    {t("Restart")}
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  disabled={!connected || locked || !workspace.trim()}
                  onClick={() => perform("start")}
                >
                  <Play />
                  {t(busy === "start" ? "Starting..." : "Start Core")}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="startup-card-title">
              <Folder size={17} />
              {t("Startup context")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="startup-fields">
              <label htmlFor="startup-mode">
                {t("Mode")}
                <Select
                  value={locked && core ? core.mode : mode}
                  onValueChange={onMode}
                  disabled={locked}
                >
                  <SelectTrigger id="startup-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {["standalone", "hub", "agent"].map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(value[0].toUpperCase() + value.slice(1))}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <label htmlFor="startup-workspace">
                {t("Workspace")}
                <div className="path-picker">
                  <Input
                    id="startup-workspace"
                    value={locked && core ? core.workspace : workspace}
                    onChange={(event) => onWorkspace(event.target.value)}
                    disabled={locked}
                    placeholder={t("Choose a workspace folder")}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={locked || !connected}
                    onClick={() => void browse()}
                  >
                    <Folder />
                    {t("Browse")}
                  </Button>
                </div>
              </label>
              <p>
                {t(
                  locked
                    ? "Stop Core before switching mode or workspace."
                    : "Used for the next Core start. Configuration comes from config.yaml.",
                )}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
      <Card className="workspace-card">
        <CardContent>
          <div className="workspace-icon">
            <Folder size={24} />
          </div>
          <div className="workspace-copy">
            <p className="eyebrow">
              {t(core ? "ACTIVE WORKSPACE" : "NEXT STARTUP WORKSPACE")}
            </p>
            <h3>
              {core?.workspace || workspace || t("No workspace selected")}
            </h3>
            <p>
              {t("Core validates access using the configured allowed roots.")}
            </p>
          </div>
        </CardContent>
      </Card>
      <div className="info-note">
        <Cable size={17} />
        <p>
          {t(
            connected
              ? "Connected to the local Python shell. Core continues running when this window closes."
              : "Waiting for the desktop Python bridge.",
          )}
        </p>
      </div>
    </>
  );
}
