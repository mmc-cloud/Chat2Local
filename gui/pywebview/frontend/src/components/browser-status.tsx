import { useEffect, useState } from "react";
import { coreApi } from "@/lib/bridge";
import { useLocale } from "@/lib/locale";
import type { BrowserStatus as Snapshot } from "@/types/pywebview";

const messages: Record<Snapshot["state"], string> = {
  disabled: "Browser is disabled",
  restart_required: "Browser configuration saved. Restart Core to apply changes.",
  listening: "Bridge is listening. Chrome extension is not connected.",
  connected: "Chrome extension is connected",
  failed: "Bridge failed to start. Check the token file and local port.",
  starting: "Browser Bridge is starting",
  core_stopped: "Browser is configured. Start Core to connect.",
  configuration_error: "Browser configuration is invalid",
  unavailable: "Browser state unavailable. Restart an updated Core to enable status reporting.",
};
export function BrowserStatus() {
  const { t } = useLocale();
  const [state, setState] = useState<Snapshot["state"] | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await coreApi.browserStatus();
        if (active) setState(result.state);
      } catch { if (active) setState("unavailable"); }
      finally { if (active) timer = setTimeout(() => void poll(), 3000); }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  return <p className="full-field text-sm" role="status">{t(state ? messages[state] : "Loading browser state...")}</p>;
}
