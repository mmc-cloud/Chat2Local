import { useCallback, useEffect, useRef, useState } from "react";
import { Search, Radio, Terminal } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { coreApi } from "@/lib/bridge";
import { usePoll } from "@/lib/poll";
import { useLocale } from "@/lib/locale";
import type { LogCursor } from "@/types/pywebview";
interface LogLine {
  id: number;
  text: string;
  level: string;
}
export function Logs() {
  const { t } = useLocale();
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("ALL");
  const [follow, setFollow] = useState(true);
  const [lines, setLines] = useState<LogLine[]>([]);
  const cursor = useRef<LogCursor | null>(null);
  const serial = useRef(0);
  const lastLevel = useRef("INFO");
  const viewer = useRef<HTMLDivElement>(null);
  const load = useCallback(async () => coreApi.logs(cursor.current), []);
  const { data, error } = usePoll(load, 1000, follow);
  useEffect(() => {
    if (!data) return;
    cursor.current = data.cursor;
    if (data.reset) lastLevel.current = "INFO";
    const added = data.lines.map((text) => {
      const match = text.match(
        /(?:^|\s)(DEBUG|INFO|WARNING|ERROR|CRITICAL)(?:\s|$)/,
      );
      if (match) lastLevel.current = match[1];
      return { id: serial.current++, text, level: lastLevel.current };
    });
    setLines((previous) =>
      (data.reset ? added : [...previous, ...added]).slice(-2000),
    );
  }, [data]);
  useEffect(() => {
    if (follow && viewer.current)
      viewer.current.scrollTop = viewer.current.scrollHeight;
  }, [follow, lines, query, level]);
  const filtered = lines.filter(
    (line) =>
      (level === "ALL" || line.level === level) &&
      line.text.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div className="logs-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">{t("ACTIVITY STREAM")}</p>
          <h1>{t("Logs")}</h1>
          <p>{t("Read-only view of the local Core log.")}</p>
        </div>
      </div>
      {error && (
        <div className="error-note" role="alert">
          {t(error)}
        </div>
      )}
      <div className="log-toolbar">
        <div className="search-input">
          <Search size={16} />
          <Input
            aria-label={t("Search logs")}
            placeholder={t("Search logs...")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <Select value={level} onValueChange={setLevel}>
          <SelectTrigger aria-label={t("Log level")} className="level-select">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].map(
              (value) => (
                <SelectItem key={value} value={value}>
                  {t(value === "ALL" ? "All levels" : value)}
                </SelectItem>
              ),
            )}
          </SelectContent>
        </Select>
        <div className="follow-control">
          <Radio size={15} />
          <label htmlFor="follow">{t("Follow")}</label>
          <Switch id="follow" checked={follow} onCheckedChange={setFollow} />
        </div>
      </div>
      <div
        className="log-viewer"
        ref={viewer}
        role="region"
        aria-label={t("Core log output")}
        tabIndex={0}
      >
        <div className="log-date">
          <Terminal size={14} />
          ~/.chat2local/logs/chat2local.log
        </div>
        {filtered.length ? (
          filtered.map((line) => (
            <div
              className={`real-log-line level-${line.level.toLowerCase()}`}
              key={line.id}
            >
              {line.text}
            </div>
          ))
        ) : (
          <div className="log-empty">
            {t(
              data?.missing
                ? "No log file yet. Logs appear when Core starts."
                : lines.length
                  ? "No logs match your filters."
                  : "Waiting for log entries...",
            )}
          </div>
        )}
      </div>
      <div className="log-footer">
        <span>
          {t("{shown} of {total} lines", {
            shown: filtered.length,
            total: lines.length,
          })}
        </span>
        <span>{t(follow ? "Following" : "Paused")}</span>
      </div>
    </div>
  );
}
