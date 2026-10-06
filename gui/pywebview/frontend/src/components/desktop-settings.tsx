import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { desktopApi, errorMessage } from "@/lib/bridge";
import { useLocale } from "@/lib/locale";
import type { DesktopChanges, DesktopSnapshot } from "@/types/pywebview";

export function DesktopSettings() {
  const { t } = useLocale();
  const [snapshot, setSnapshot] = useState<DesktopSnapshot>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    desktopApi
      .readPreferences()
      .then((data) => {
        if (active) setSnapshot(data);
      })
      .catch((error) => {
        if (active) setError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, []);

  const save = async (changes: DesktopChanges) => {
    setBusy(true);
    setError("");
    try {
      setSnapshot(await desktopApi.savePreferences(changes));
    } catch (error) {
      const message = errorMessage(error);
      setError(message);
      toast.error(t(message));
      // Keep controls on the last confirmed state, then reread after a failure.
      try {
        setSnapshot(await desktopApi.readPreferences());
      } catch {
        /* Keep the confirmed snapshot. */
      }
    } finally {
      setBusy(false);
    }
  };
  const preferences = snapshot?.preferences;
  const toggles = [
    ["launch_at_login", "Launch Chat2Local when I sign in to Windows"],
    ["silent_login_start", "Start silently when launched at sign-in"],
    ["auto_start_core", "Start Core automatically"],
  ] as const;
  return (
    <section className="settings-section">
      <div className="settings-intro">
        <h2>{t("Desktop")}</h2>
        <p>{t("Saved immediately. Separate from Core configuration.")}</p>
      </div>
      <div className="settings-fields">
        {error && (
          <div className="error-note full-field" role="alert">
            {t(error)}
          </div>
        )}
        {!snapshot && !error && <p>{t("Loading desktop preferences...")}</p>}
        {preferences && (
          <>
            {toggles.map(([key, label]) => (
              <div className="config-field" key={key}>
                <label htmlFor={`desktop-${key}`}>{t(label)}</label>
                <Switch
                  id={`desktop-${key}`}
                  checked={preferences[key]}
                  disabled={
                    busy ||
                    (key === "launch_at_login" &&
                      !snapshot.launch_at_login_supported) ||
                    (key === "silent_login_start" &&
                      !preferences.launch_at_login)
                  }
                  onCheckedChange={(value) => {
                    void save({ [key]: value });
                  }}
                />
              </div>
            ))}
            <div className="config-field">
              <label htmlFor="desktop-close">{t("Close button")}</label>
              <Select
                value={preferences.close_behavior}
                disabled={busy}
                onValueChange={(value) => {
                  void save({
                    close_behavior: value === "exit" ? "exit" : "tray",
                  });
                }}
              >
                <SelectTrigger id="desktop-close">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="tray">{t("Hide to tray")}</SelectItem>
                  <SelectItem value="exit">{t("Exit Chat2Local")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {!snapshot.tray_available && (
              <p className="full-field">
                {t("Tray unavailable; closing the window will exit Chat2Local")}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
