import { useEffect, useState } from "react";
import { autostartApi, errorMessage } from "@/lib/bridge";
import { useLocale } from "@/lib/locale";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import type { AutostartSnapshot } from "@/types/pywebview";

export function AutostartSettings() {
  const { t } = useLocale();
  const [snapshot, setSnapshot] = useState<AutostartSnapshot>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    autostartApi.status().then((value) => { if (active) setSnapshot(value); })
      .catch((error) => { if (active) setError(errorMessage(error)); });
    return () => { active = false; };
  }, []);
  const update = async (enabled?: boolean) => {
    setBusy(true);
    setError("");
    try {
      const value = enabled === undefined ? await autostartApi.status() : await autostartApi.setEnabled(enabled);
      setSnapshot(value);
    }
    catch (error) {
      setError(errorMessage(error));
      try {
        const value = await autostartApi.status();
        setSnapshot(value);
      } catch { /* Preserve last confirmed state. */ }
    } finally { setBusy(false); }
  };
  return <section className="settings-section">
    <div className="settings-intro">
      <h2>{t("Core autostart")}</h2>
      <p>{t("Saved immediately. Changes future startup without stopping the current Core.")}</p>
    </div>
    <div className="settings-fields">
      {error && <div className="error-note full-field" role="alert">{t(error)}</div>}
      <div className="config-field">
        <label htmlFor="core-autostart">{t("Start independent Core at login or boot")}</label>
        <Switch id="core-autostart" checked={snapshot?.enabled === true}
          disabled={busy || !snapshot?.supported || snapshot.enabled === null}
          onCheckedChange={(enabled) => void update(enabled)} />
      </div>
      <div className="config-field">
        <Button type="button" variant="outline" disabled={busy} onClick={() => void update()}>{t("Refresh startup state")}</Button>
      </div>
      {snapshot && <div className="full-field">
        <p>{snapshot.backend} · {snapshot.scope} · {snapshot.service} · {snapshot.state}</p>
        {snapshot.path && <p>{snapshot.path}</p>}
        {snapshot.state === "stale" ? <>
          <p role="alert">{t("Startup entry is outdated or invalid. Repair it for the current Core, or remove it.")}</p>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void update(true)}>{t("Repair startup entry")}</Button>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void update(false)}>{t("Remove startup entry")}</Button>
        </> : snapshot.enabled === null && <p>{t("This service has no editable persistent startup state.")}</p>}
      </div>}
    </div>
  </section>;
}
