import { Monitor, Server, Laptop, Globe, Cpu } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { coreApi } from "@/lib/bridge";
import { usePoll } from "@/lib/poll";
import { useLocale } from "@/lib/locale";
export function Devices() {
  const { t } = useLocale();
  const { data, error } = usePoll(coreApi.devices, 2000);
  const devices = data?.devices ?? [];
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{t("DEVICE NETWORK")}</p>
          <h1>{t("Devices")}</h1>
          <p>{t("Devices currently known to your local Core.")}</p>
        </div>
        {data?.mode && <Badge variant="secondary">{t(data.mode)}</Badge>}
      </div>
      {error && (
        <div className="error-note" role="alert">
          {t(error)}
        </div>
      )}
      <div className="device-stats">
        <span>{t("{count} connected devices", { count: devices.length })}</span>
      </div>
      <div className="device-list">
        {devices.map((device) => {
          const Icon =
            device.kind === "remote"
              ? Monitor
              : data?.mode === "hub"
                ? Server
                : Laptop;
          return (
            <Card key={device.device_id}>
              <CardContent className="device-row">
                <div className="workspace-icon">
                  <Icon size={24} />
                </div>
                <div className="device-copy">
                  <div className="device-title">
                    <h3>{device.device_id}</h3>
                    {device.kind === "local" && (
                      <Badge variant="outline">{t("This device")}</Badge>
                    )}
                  </div>
                  <div className="device-meta">
                    <span>
                      <Globe size={13} />
                      {t(device.kind)}
                    </span>
                    <span>
                      <Cpu size={13} />
                      {t("{count} capabilities", {
                        count: device.tools.length,
                      })}
                    </span>
                  </div>
                </div>
                <StatusBadge
                  online={device.online}
                  label={t(device.online ? "Online" : "Offline")}
                />
              </CardContent>
            </Card>
          );
        })}
      </div>
      {!devices.length && !error && (
        <div className="info-note">
          <Server size={17} />
          <p>
            {t(
              data
                ? "No active Core. Start Core from Overview to see devices."
                : "Loading device snapshot...",
            )}
          </p>
        </div>
      )}
      {data?.mode === "agent" && (
        <div className="info-note">
          <Globe size={17} />
          <p>
            {t(
              "Agent mode shows this device. The Hub network is managed by the Hub.",
            )}
          </p>
        </div>
      )}
    </>
  );
}
