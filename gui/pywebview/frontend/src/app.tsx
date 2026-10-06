import { useCallback, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppSidebar, type Page } from "@/components/app-sidebar";
import { Overview } from "@/pages/overview";
import { Devices } from "@/pages/devices";
import { Settings } from "@/pages/settings";
import { Logs } from "@/pages/logs";
import { coreApi, desktopApi, useBridge } from "@/lib/bridge";
import { usePoll } from "@/lib/poll";
import { useLocale } from "@/lib/locale";
export function App() {
  const { t } = useLocale();
  const [page, setPage] = useState<Page>("Overview");
  const [mode, setMode] = useState("standalone");
  const [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState("");
  const discoveredInstance = useRef<string | null>(null);
  const bridge = useBridge();
  const loadRuntime = useCallback(async () => {
    const [snapshot, navigation] = await Promise.all([
      coreApi.status(),
      desktopApi.takeNavigation(),
    ]);
    if (navigation === "Logs") setPage("Logs");
    const core = snapshot.core;
    if (snapshot.lifecycle !== "Running" || core?.mode !== "hub") {
      setPage((current) => (current === "Devices" ? "Overview" : current));
    }
    if (
      snapshot.lifecycle === "Running" &&
      core &&
      core.instance_id !== discoveredInstance.current
    ) {
      discoveredInstance.current = core.instance_id;
      setMode(core.mode);
      setWorkspace(core.workspace);
    }
    return snapshot;
  }, []);
  const { data: runtime, error } = usePoll(
    loadRuntime,
    1000,
    bridge.status === "connected",
  );
  const currentMode =
    runtime?.lifecycle === "Running" && runtime.core ? runtime.core.mode : mode;
  const showDevices =
    runtime?.lifecycle === "Running" && runtime.core?.mode === "hub";
  const currentPage = page === "Devices" && !showDevices ? "Overview" : page;
  const perform = async (action: "start" | "stop" | "restart") => {
    setBusy(action);
    try {
      if (action === "start") await coreApi.start(mode, workspace);
      else if (action === "stop") await coreApi.stop();
      else await coreApi.restart();
    } finally {
      setBusy("");
    }
  };
  return (
    <TooltipProvider>
      <div className="app-shell">
        <AppSidebar
          page={currentPage}
          onNavigate={setPage}
          bridge={bridge}
          showDevices={showDevices}
        />
        <div className="main-shell">
          <header className="topbar">
            <div>
              <span>{t("Workspace")}</span>
              <ChevronRight size={14} />
              <strong>{t(currentPage)}</strong>
            </div>
            <span>{t(runtime?.lifecycle ?? "Connecting...")}</span>
          </header>
          <main
            key={currentPage}
            className={
              currentPage === "Logs"
                ? "page-content logs-page-content"
                : "page-content"
            }
          >
            {runtime?.desktop_error && (
              <div className="error-note" role="alert">
                {t(runtime.desktop_error)}
              </div>
            )}
            {currentPage === "Overview" ? (
              <Overview
                runtime={runtime}
                error={error}
                mode={mode}
                workspace={workspace}
                onMode={setMode}
                onWorkspace={setWorkspace}
                busy={busy}
                onAction={perform}
                connected={bridge.status === "connected"}
              />
            ) : currentPage === "Devices" ? (
              <Devices />
            ) : currentPage === "Settings" ? (
              <Settings currentMode={currentMode} />
            ) : (
              <Logs />
            )}
          </main>
        </div>
      </div>
      <Toaster
        theme="system"
        position="bottom-right"
        closeButton
        containerAriaLabel={t("Notifications")}
        toastOptions={{ closeButtonAriaLabel: t("Close toast") }}
      />
    </TooltipProvider>
  );
}
