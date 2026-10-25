import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import Markdown from "react-markdown";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { coreApi, errorMessage } from "@/lib/bridge";
import { useLocale } from "@/lib/locale";
import type { HandoffDetail, HandoffSnapshot, LocalHandoff } from "@/types/pywebview";

type Draft = Pick<HandoffDetail, "title" | "summary" | "content">;
const draftText = (draft: Draft) => `Title: ${draft.title}\nSummary: ${draft.summary}\n\nMarkdown:\n${draft.content}`;

export function Handoffs({ onLeaveGuard }: { onLeaveGuard?: (guard: (() => boolean) | null) => void }) {
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
  const [draft, setDraft] = useState<Draft>();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [latest, setLatest] = useState<HandoffDetail>();
  const [latestBusy, setLatestBusy] = useState(false);
  const [latestError, setLatestError] = useState("");
  const [copyNotice, setCopyNotice] = useState("");
  const [copyError, setCopyError] = useState("");
  const operationPending = useRef(false);
  const dirty = !!draft && !!detail && (draft.title !== detail.title || draft.summary !== detail.summary || draft.content !== detail.content);
  const editingBusy = saving || latestBusy;
  const canDiscard = useCallback(() => {
    if (operationPending.current) return false;
    return !dirty || window.confirm(t("Discard your unsaved handoff changes?"));
  }, [dirty, t]);
  useEffect(() => {
    onLeaveGuard?.(canDiscard);
    return () => onLeaveGuard?.(null);
  }, [canDiscard, onLeaveGuard]);
  useEffect(() => {
    if (!dirty && !editingBusy) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, editingBusy]);
  const resetEditor = () => {
    setDraft(undefined);
    setConflict(false);
    setComparing(false);
    setLatest(undefined);
    setSaveError("");
    setLatestError("");
    setCopyNotice("");
    setCopyError("");
  };
  const updateSnapshot = (updated: HandoffDetail) => {
    // Keep the browser list metadata-only, just like the transport snapshot.
    const { content: _content, ...metadata } = updated;
    void _content;
    setSnapshot((current) => current && ({ ...current, handoffs: current.handoffs.map((entry) => entry.id === updated.id ? metadata : entry) }));
  };
  const save = async () => {
    if (!draft || !detail || operationPending.current) return;
    operationPending.current = true;
    setSaving(true);
    setSaveError("");
    try {
      const updated = await coreApi.saveLocalHandoff(detail.id, draft.title, draft.summary, draft.content, detail.revision);
      setDetail(updated);
      updateSnapshot(updated);
      resetEditor();
    } catch (error) {
      const message = errorMessage(error);
      setSaveError(message);
      setConflict(message.startsWith("revision_conflict:"));
      if (message.startsWith("revision_conflict:")) {
        setLatest(undefined);
      }
    } finally {
      operationPending.current = false;
      setSaving(false);
    }
  };
  const readLatest = async (replaceDraft = false) => {
    if (!detail || operationPending.current) return;
    if (replaceDraft && !window.confirm(t("Load the latest version and discard your current draft?"))) return;
    operationPending.current = true;
    setLatestBusy(true);
    setLatestError("");
    setLatest(undefined);
    try {
      const next = await coreApi.localHandoff(detail.id);
      setLatest(next);
      if (replaceDraft) {
        setDetail(next);
        updateSnapshot(next);
        resetEditor();
        setDraft({ title: next.title, summary: next.summary, content: next.content });
      }
    } catch (error) {
      setLatestError(errorMessage(error));
    } finally {
      operationPending.current = false;
      setLatestBusy(false);
    }
  };
  const copyDraft = async () => {
    if (!draft) return;
    setCopyNotice("");
    setCopyError("");
    try {
      await navigator.clipboard.writeText(draftText(draft));
      setCopyNotice("Your changes were copied.");
    } catch {
      setCopyError("Could not copy your changes. Select and copy the text from the comparison instead.");
    }
  };
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
    if (!canDiscard()) return;
    resetEditor();
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
    if (loading || !canDiscard()) return;
    resetEditor();
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
      <Button variant="outline" onClick={reload} disabled={loading || editingBusy}>
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
              aria-pressed={selected === entry.id} disabled={editingBusy} onClick={() => select(entry.id)}>
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
            <div className="handoff-detail-heading"><h2>{detail.title}</h2>
              {!draft && <Button variant="outline" onClick={() => {
                resetEditor();
                setDraft({ title: detail.title, summary: detail.summary, content: detail.content });
              }}>{t("Edit handoff")}</Button>}
            </div>
            <dl className="handoff-metadata">
              <dt>{t("Project")}</dt><dd>{detail.project.scope === "personal" ? t("Personal") : detail.project.name}
                {detail.project.path && <span className="handoff-path">{detail.project.path}</span>}</dd>
              <dt>{t("Workstream")}</dt><dd>{detail.workstream}</dd>
              {!draft && <><dt>{t("Summary")}</dt><dd>{detail.summary}</dd></>}
              <dt>{t("Revision")}</dt><dd>{detail.revision}</dd>
              <dt>{t("Updated At")}</dt><dd><time dateTime={detail.updated_at}>{detail.updated_at}</time></dd>
            </dl>
            {draft ? <div className="handoff-editor">
              {saveError && <div className="error-note" role="alert">
                {t(conflict ? "This handoff has a newer revision. Your draft has been kept." : "Could not save this handoff. Your draft has been kept.")}
                <p>{saveError}</p>
              </div>}
              {conflict && <div className="handoff-actions">
                <Button variant="outline" disabled={editingBusy} onClick={() => { setComparing(true); void readLatest(); }}>{t("Compare versions")}</Button>
                <Button variant="outline" onClick={() => void copyDraft()}>{t("Copy my changes")}</Button>
                <Button variant="outline" disabled={editingBusy} onClick={() => void readLatest(true)}>{t("Load latest version")}</Button>
                <Button variant="outline" disabled={editingBusy} onClick={() => setComparing(false)}>{t("Return to editing")}</Button>
              </div>}
              {copyNotice && <p role="status">{t(copyNotice)}</p>}
              {copyError && <p className="error-note" role="alert">{t(copyError)}</p>}
              {latestError && <div className="error-note" role="alert">{t("Could not load the latest version. Your draft has been kept.")}<p>{latestError}</p></div>}
              {latestBusy && <p role="status">{t("Loading latest version...")}</p>}
              {comparing ? <div className="handoff-comparison">
                <label>{t("My changes")}<textarea readOnly aria-label={t("My changes")} value={draftText(draft)} /></label>
                <label>{t("Latest version")}<textarea readOnly aria-label={t("Latest version")}
                  value={latest ? `Revision: ${latest.revision}\nUpdated At: ${latest.updated_at}\n${draftText(latest)}` : ""} /></label>
              </div> : <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
                <fieldset disabled={editingBusy}>
                  <legend>{t("Edit handoff")}</legend>
                  <label htmlFor="handoff-title">{t("Title")}</label>
                  <Input id="handoff-title" value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
                  <label htmlFor="handoff-summary">{t("Summary")}</label>
                  <Input id="handoff-summary" value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} />
                  <label htmlFor="handoff-content">{t("Markdown content")}</label>
                  <textarea id="handoff-content" value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} />
                  <div className="handoff-actions">
                    <Button type="submit" disabled={!dirty || editingBusy}>{t(saving ? "Saving..." : "Save changes")}</Button>
                    <Button type="button" variant="outline" onClick={() => { if (canDiscard()) resetEditor(); }}>{t("Cancel editing")}</Button>
                  </div>
                </fieldset>
              </form>}
            </div> : <article className="handoff-markdown" aria-label={t("Markdown content")}>
              <Markdown skipHtml disallowedElements={["img"]} components={{
                // Only document anchors are active in the local reader.
                a: ({ href, children }) => href?.startsWith("#")
                  ? <a href={href}>{children}</a> : <span>{children}</span>,
              }}>{detail.content}</Markdown>
            </article>}
          </>}
      </section>
    </div>
  </div>;
}
