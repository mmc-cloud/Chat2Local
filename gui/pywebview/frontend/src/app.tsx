import { useCallback, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppSidebar, type Page } from "@/components/app-sidebar";
import { Overview } from "@/pages/overview";
import { Devices } from "@/pages/devices";
import { Settings } from "@/pages/settings";
import { Logs } from "@/pages/logs";
import { coreApi, useBridge } from "@/lib/bridge";
import { usePoll } from "@/lib/poll";
export function App() {
  const [page, setPage] = useState<Page>("Overview");
  const [mode, setMode] = useState("standalone");
  const [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState("");
  const discoveredInstance = useRef<string | null>(null);
  const bridge = useBridge();
  const loadRuntime = useCallback(async () => {
    const snapshot = await coreApi.status();
    const core = snapshot.core;
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
        <AppSidebar page={page} onNavigate={setPage} bridge={bridge} />
        <div className="main-shell">
          <header className="topbar">
            <div>
              <span>Workspace</span>
              <ChevronRight size={14} />
              <strong>{page}</strong>
            </div>
            <span>{runtime?.lifecycle ?? "Connecting..."}</span>
          </header>
          <main key={page} className="page-content">
            {page === "Overview" ? (
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
            ) : page === "Devices" ? (
              <Devices />
            ) : page === "Settings" ? (
              <Settings />
            ) : (
              <Logs />
            )}
          </main>
        </div>
      </div>
      <Toaster theme="system" position="bottom-right" closeButton />
    </TooltipProvider>
  );
}
