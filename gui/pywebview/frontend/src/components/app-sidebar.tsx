import {
  LayoutDashboard,
  Monitor,
  Settings2,
  ScrollText,
  Terminal,
  FlaskConical,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { BridgeState } from "@/lib/bridge";
export type Page = "Overview" | "Devices" | "Settings" | "Logs";
const navigation = [
  { name: "Overview", Icon: LayoutDashboard },
  { name: "Devices", Icon: Monitor },
  { name: "Settings", Icon: Settings2 },
  { name: "Logs", Icon: ScrollText },
] as const;
export function AppSidebar({
  page,
  onNavigate,
  bridge,
}: {
  page: Page;
  onNavigate: (page: Page) => void;
  bridge: BridgeState;
}) {
  return (
    <aside className="sidebar">
      <div className="wordmark">
        <span className="logo-mark">
          <Terminal size={19} />
        </span>
        <strong>Chat2Local</strong>
      </div>
      <p className="sidebar-label">WORKSPACE</p>
      <nav aria-label="Main navigation">
        {navigation.map(({ name, Icon }) => (
          <Button
            key={name}
            variant="ghost"
            className={page === name ? "nav-item active" : "nav-item"}
            aria-current={page === name ? "page" : undefined}
            onClick={() => onNavigate(name)}
          >
            <Icon size={17} />
            {name}
          </Button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="prototype-pill">
          <FlaskConical size={15} />
          <span>Local control</span>
          <span className="stage-dot">02</span>
        </div>
        <p>
          Local by design.
          <br />
          Connected when you need it.
        </p>
        <Separator />
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="sidebar-connection" tabIndex={0}>
              <i
                className={
                  bridge.status === "connected"
                    ? "status-dot online"
                    : "status-dot"
                }
              />
              <span>
                {bridge.status === "connected"
                  ? "Backend connected"
                  : bridge.status === "connecting"
                    ? "Connecting..."
                    : "Backend unavailable"}
              </span>
            </div>
          </TooltipTrigger>
          <TooltipContent>
            Live connection to the local Python shell.
          </TooltipContent>
        </Tooltip>
        <div className="version-line">
          <span>GUI Stage 2</span>
          <span>Core {bridge.info?.version ?? "—"}</span>
        </div>
      </div>
    </aside>
  );
}
