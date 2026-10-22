import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { coreApi, errorMessage } from "@/lib/bridge";
import { useLocale } from "@/lib/locale";
import type { CredentialInfo, TokenPurpose } from "@/types/pywebview";

export function TokenManager({ purpose, path, onPath }: {
  purpose: TokenPurpose; path: string | null; onPath: (path: string) => void;
}) {
  const { t } = useLocale();
  const [metadata, setInfo] = useState<CredentialInfo>();
  const [loadedFor, setLoadedFor] = useState("");
  const requestKey = `${purpose}:${path ?? ""}`;
  const info = loadedFor === requestKey ? metadata : undefined;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [imported, setImported] = useState("");
  const [confirm, setConfirm] = useState(false);
  useEffect(() => {
    let active = true;
    coreApi.credentialInfo(purpose, path).then((value) => {
      if (active) { setInfo(value); setLoadedFor(requestKey); setConfirm(false); setError(""); }
    }).catch((reason) => { if (active) { setInfo(undefined); setError(errorMessage(reason)); } });
    return () => { active = false; };
  }, [purpose, path, requestKey]);

  const perform = async (operation: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await operation(); }
    catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };
  const saveToken = (regenerate = false) => perform(async () => {
    const result = await coreApi.manageToken(purpose, path, purpose === "agent" ? imported : null, regenerate, regenerate);
    setInfo(result); onPath(result.path); setImported(""); setConfirm(false);
    toast.success(t(regenerate ? "Token regenerated. Restart Core and pair the Chrome extension again." : "Token file ready. Save changes to apply its path."));
  });
  return <div className="full-field roots-editor" aria-label={t("Token management")}>
    <p className="text-muted-foreground text-xs">{t(purpose === "agent"
      ? "Import the token supplied by your Hub. Agent never generates a replacement shared token."
      : purpose === "hub" ? "Hub and Agents share this token. Existing credential files are kept."
      : "Browser uses an independent token. Copy it into the Chrome extension options after saving.")}</p>
    {purpose !== "browser" && <p className="text-muted-foreground text-xs">{t("These actions manage token files. CLI and CHAT2LOCAL_HUB_TOKEN overrides still take precedence.")}</p>}
    {info && <p className="text-xs" role="status">{t(`Token file: ${info.state}`)} · {info.path}</p>}
    {error && <p className="error-note" role="alert">{t(error)}</p>}
    {purpose === "agent" && <Input type="password" autoComplete="off" aria-label={t("Hub token to import")}
      value={imported} onChange={(event) => setImported(event.target.value)} placeholder={t("Paste the existing Hub token")} />}
    <div className="flex flex-wrap gap-2">
      {purpose !== "agent" && <Button type="button" variant="outline" size="sm" disabled={busy}
        onClick={() => void saveToken()}>{t("Generate or reuse token")}</Button>}
      {purpose === "agent" && <>
        <Button type="button" variant="outline" size="sm" disabled={busy || !imported}
          onClick={() => void saveToken()}>{t("Import Hub token")}</Button>
        <Button type="button" variant="outline" size="sm" disabled={busy || info?.state !== "ready"}
          onClick={() => { if (info) onPath(info.path); }}>{t("Use existing token file")}</Button>
      </>}
      <Button type="button" variant="outline" size="sm" disabled={busy || info?.state !== "ready"}
        onClick={() => void perform(async () => {
          await coreApi.copyToken(purpose, info?.path ?? path);
          toast.success(t("Token copied to clipboard"));
        })}>{t("Copy token")}</Button>
      {purpose === "browser" && <Button type="button" variant="outline" size="sm" disabled={busy || info?.state !== "ready"}
        onClick={() => setConfirm(true)}>{t("Regenerate token")}</Button>}
    </div>
    {confirm && info && <div role="alert" className="error-note">
      <p>{t("Regenerating invalidates the previous Browser token. Restart Core and pair the Chrome extension again. Continue?")}</p>
      <Button type="button" size="sm" disabled={busy} onClick={() => void saveToken(true)}>{t("Confirm regeneration")}</Button>
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setConfirm(false)}>{t("Cancel")}</Button>
    </div>}
  </div>;
}
