import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import Markdown from "react-markdown";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { coreApi, errorMessage } from "@/lib/bridge";
import { useLocale } from "@/lib/locale";
import type { HandoffDetail, HandoffSnapshot, LocalHandoff } from "@/types/pywebview";

export function Handoffs() {
  const { t } = useLocale();
  const [snapshot, setSnapshot] = useState<HandoffSnapshot>();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(() => new Set());
  const [searchCollapse, setSearchCollapse] = useState<{ query: string; keys: Set<string> }>(
    () => ({ query: "", keys: new Set() }),
  );
  const detailRef = useRef<HTMLElement>(null);
  const [detail, setDetail] = useState<HandoffDetail>();
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    coreApi.localHandoffs().then((next) => {
      if (!active) return;
      setSnapshot(next);
      setSelected((current) => next.handoffs.some((entry) => entry.id === current) ? current : null);
    }).catch((error) => {
      if (active) setListError(errorMessage(error));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    coreApi.localHandoff(selected).then((next) => {
      if (active) setDetail(next);
    }).catch((error) => {
      if (active) setDetailError(errorMessage(error));
    }).finally(() => { if (active) setReading(false); });
    return () => { active = false; };
  }, [selected, refresh]);
  const groups = useMemo(() => {
    const result = new Map<string, LocalHandoff[]>();
    const search = query.trim().toLocaleLowerCase();
    for (const entry of snapshot?.handoffs ?? []) {
      const project = entry.project.scope === "personal" ? t("Personal") : entry.project.name;
      if (![entry.title, entry.summary, project].some((text) => text.toLocaleLowerCase().includes(search))) continue;
      const group = result.get(entry.project.key) ?? [];
      group.push(entry);
      result.set(entry.project.key, group);
    }
    return [...result.values()].sort((a, b) =>
      a[0].project.scope === b[0].project.scope
        ? a[0].project.name.localeCompare(b[0].project.name)
        : a[0].project.scope === "personal" ? -1 : 1);
  }, [snapshot, query, t]);
  const select = (id: string) => {
    if (id === selected) return;
    if (detailRef.current) detailRef.current.scrollTop = 0;
    setDetail(undefined);
    setDetailError("");
    setReading(true);
    setSelected(id);
  };
  const toggleProject = (key: string) => {
    const search = query.trim();
    if (search) {
      setSearchCollapse((previous) => {
        const next = new Set(previous.query === search ? previous.keys : []);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return { query: search, keys: next };
      });
      return;
    }
    setExpandedProjects((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const reload = () => {
    setLoading(true);
    setReading(selected !== null);
    setDetail(undefined);
    setListError("");
    setDetailError("");
    setRefresh((current) => current + 1);
  };
  return <div className="handoffs-page">
    <div className="page-heading">
      <div><p className="eyebrow">{t("LOCAL HANDOFFS")}</p><h1>{t("Handoffs")}</h1>
        <p>{t("Browse handoffs from every project on this device.")}</p></div>
      <Button variant="outline" onClick={reload} disabled={loading}>
        <RefreshCw size={15} />{t("Refresh")}
      </Button>
    </div>
    {listError && <div className="error-note" role="alert">{t(listError)}</div>}
    {!!snapshot?.issues.length && <details className="error-note">
      <summary>{t("Some handoffs could not be read ({count}).", { count: snapshot.issues.length })}</summary>
      <ul>{snapshot.issues.map((issue, index) => <li key={index}><strong>{issue.location}</strong>: {issue.message}</li>)}</ul>
    </details>}
    <div className="handoff-layout">
      <section className="handoff-list" aria-label={t("Handoff list")} aria-busy={loading}>
        <Input aria-label={t("Search handoffs")} placeholder={t("Search title, summary or project...")}
          value={query} onChange={(event) => setQuery(event.target.value)} />
        <div className="handoff-groups">
        {loading ? <p role="status">{t("Loading handoffs...")}</p> : groups.length ? groups.map((entries) => {
          const project = entries[0].project;
          const search = query.trim();
          const expanded = search
            ? !(searchCollapse.query === search && searchCollapse.keys.has(project.key))
            : expandedProjects.has(project.key);
          return <section key={project.key} className="handoff-group">
            <h2><button type="button" className="handoff-group-toggle" aria-expanded={expanded}
              onClick={() => toggleProject(project.key)}>
              <ChevronRight size={15} aria-hidden="true" className={expanded ? "expanded" : ""} />
              <span>{project.scope === "personal" ? t("Personal") : project.name}</span>
              <span className="handoff-group-count">{entries.length}</span>
            </button></h2>
            {expanded && <div className="handoff-group-body">
              {project.path && <p className="handoff-path">{project.path}</p>}
              {entries.map((entry) => <button key={entry.id} type="button"
              className={`handoff-item${selected === entry.id ? " selected" : ""}`}
              aria-pressed={selected === entry.id} onClick={() => select(entry.id)}>
              <strong>{entry.title}</strong><span>{entry.summary}</span>
              </button>)}
            </div>}
          </section>;
        }) : !listError && <p role="status">{t(snapshot?.handoffs.length ? "No handoffs match your search." : "No local handoffs yet.")}</p>}
        </div>
      </section>
      <section ref={detailRef} className="handoff-detail" aria-label={t("Handoff details")} aria-busy={selected !== null && reading}>
        {!selected ? <p role="status">{t("Select a handoff to read its content.")}</p>
          : reading ? <p role="status">{t("Loading handoff...")}</p>
          : detailError ? <div className="error-note" role="alert">{t("Could not read this handoff. It may have moved or been deleted.")}<p>{t(detailError)}</p></div>
          : detail && <>
            <h2>{detail.title}</h2>
            <dl className="handoff-metadata">
              <dt>{t("Project")}</dt><dd>{detail.project.scope === "personal" ? t("Personal") : detail.project.name}
                {detail.project.path && <span className="handoff-path">{detail.project.path}</span>}</dd>
              <dt>{t("Workstream")}</dt><dd>{detail.workstream}</dd>
              <dt>{t("Summary")}</dt><dd>{detail.summary}</dd>
              <dt>{t("Revision")}</dt><dd>{detail.revision}</dd>
              <dt>{t("Updated At")}</dt><dd><time dateTime={detail.updated_at}>{detail.updated_at}</time></dd>
            </dl>
            <article className="handoff-markdown" aria-label={t("Markdown content")}>
              <Markdown skipHtml disallowedElements={["img"]} components={{
                // Only document anchors are active in the local reader.
                a: ({ href, children }) => href?.startsWith("#")
                  ? <a href={href}>{children}</a> : <span>{children}</span>,
              }}>{detail.content}</Markdown>
            </article>
          </>}
      </section>
    </div>
  </div>;
}
