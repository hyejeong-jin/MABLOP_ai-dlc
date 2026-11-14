// Result view: draft history in the style of a chat application (Req 23).
// Left: the user's drafts, newest first (list-drafts). Right: the body of the selected draft
// (get-result, kind "draft") with copy / download. Keeps no copy of a draft in the browser.
// get-result and delete-draft need no Grant, so this view sends no grantId (Req 20.8, 20.9).
import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, Copy, Download, Info, Trash2 } from "lucide-react";
import { postJson } from "../api";
import type { DraftProvenance } from "../api";
import { SourceLabel } from "../components/SourceLabel";
import { safeExternalUrl } from "../components/ResearchSummary";
import { formatKst } from "../format";

export interface ResultViewProps {
  focusDraftId?: string | null; // draft just generated; selected once, then onFocusHandled
  onFocusHandled?: () => void;
}

interface DraftEntry {
  draftId: string;
  title: string;
  createdAt: string;
}

interface ListDraftsResponse {
  drafts?: DraftEntry[];
}

interface GetResultResponse {
  markdown?: string;
  provenance?: DraftProvenance | null;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

// Newest first; ties by draftId descending (same order as the backend).
function sortNewestFirst(entries: DraftEntry[]): DraftEntry[] {
  return [...entries].sort((a, b) => {
    if (a.createdAt !== b.createdAt) return a.createdAt < b.createdAt ? 1 : -1;
    if (a.draftId !== b.draftId) return a.draftId < b.draftId ? 1 : -1;
    return 0;
  });
}

export function ResultView({ focusDraftId = null, onFocusHandled }: ResultViewProps) {
  const [drafts, setDrafts] = useState<DraftEntry[]>([]);
  const [listLoaded, setListLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [body, setBody] = useState<string | null>(null);
  const [provenance, setProvenance] = useState<DraftProvenance | null>(null);
  const [bodyLoading, setBodyLoading] = useState(false);
  const [bodyError, setBodyError] = useState("");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState(false);

  // Refs let async handlers compare against the *current* state (late responses are discarded).
  const selectedRef = useRef<string | null>(null);
  const listSeq = useRef(0);
  const initialized = useRef(false);
  const handledRef = useRef(onFocusHandled);
  handledRef.current = onFocusHandled;

  const select = useCallback(async (id: string) => {
    selectedRef.current = id;
    setSelectedId(id);
    setBody(null);
    setProvenance(null);
    setBodyError("");
    setCopied(false);
    setBodyLoading(true);
    try {
      const res = await postJson<GetResultResponse>({ action: "get-result", kind: "draft", id });
      if (selectedRef.current !== id) return;
      setBody(typeof res?.markdown === "string" ? res.markdown : "");
      setProvenance(res?.provenance && typeof res.provenance === "object" ? res.provenance : null);
    } catch (err) {
      if (selectedRef.current !== id) return;
      setBodyError(errorMessage(err, "초안 본문을 불러오지 못했습니다."));
    } finally {
      if (selectedRef.current === id) setBodyLoading(false);
    }
  }, []);

  const loadList = useCallback(
    async (focusId: string | null) => {
      const seq = ++listSeq.current;
      let selectTarget: string | null = null;
      try {
        const res = await postJson<ListDraftsResponse>({ action: "list-drafts" });
        if (seq !== listSeq.current) return;
        const entries = Array.isArray(res?.drafts) ? res.drafts : [];
        setDrafts(sortNewestFirst(entries));
        setListError("");
        if (focusId && entries.some((d) => d.draftId === focusId)) selectTarget = focusId;
      } catch (err) {
        if (seq !== listSeq.current) return;
        setListError(errorMessage(err, "초안 목록을 불러오지 못했습니다."));
      }
      setListLoaded(true);
      if (focusId) handledRef.current?.(); // the hand-off is consumed once, listed or not
      if (selectTarget) void select(selectTarget);
    },
    [select],
  );

  // Open: list-drafts. A later non-null focusDraftId reloads the list and selects it once.
  useEffect(() => {
    if (initialized.current && !focusDraftId) return;
    initialized.current = true;
    void loadList(focusDraftId);
  }, [focusDraftId, loadList]);

  function askDelete(id: string) {
    setDeleteError((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setConfirmingId(id);
  }

  async function confirmDelete(id: string) {
    setDeletingId(id);
    try {
      await postJson({ action: "delete-draft", draftId: id });
      setConfirmingId(null);
      if (selectedRef.current === id) {
        selectedRef.current = null;
        setSelectedId(null);
        setBody(null);
        setProvenance(null);
        setBodyError("");
        setBodyLoading(false);
        setCopied(false);
      }
      await loadList(null);
    } catch (err) {
      setConfirmingId(null);
      setDeleteError((prev) => ({ ...prev, [id]: errorMessage(err, "초안을 삭제하지 못했습니다.") }));
    } finally {
      setDeletingId(null);
    }
  }

  // native clipboard copy; no deps
  async function copy() {
    if (!body) return;
    try {
      await navigator.clipboard.writeText(body);
      setCopied(true);
    } catch {
      setBodyError("복사하지 못했습니다. 브라우저의 클립보드 권한을 확인하세요.");
    }
  }

  // download raw markdown as {draftId}.md via blob url
  function download() {
    if (!body || !selectedId) return;
    const blob = new Blob([body], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const isEmpty = listLoaded && !listError && drafts.length === 0;

  return (
    <section>
      <h2 className="page-title">결과</h2>
      <p className="page-lead">지금까지 만든 초안입니다. 항목을 선택하면 본문을 볼 수 있어요.</p>

      {listError && (
        <div className="callout callout-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <p>{listError}</p>
        </div>
      )}

      <div className="history-layout">
        <div>
          {!listLoaded && (
            <p className="meta" role="status">
              초안 목록을 불러오는 중...
            </p>
          )}
          {isEmpty && <p className="meta">아직 만든 초안이 없습니다.</p>}
          {drafts.length > 0 && (
            <ul className="history-list" aria-label="초안 기록">
              {drafts.map((d) => {
                const selected = d.draftId === selectedId;
                const confirming = d.draftId === confirmingId;
                return (
                  <li
                    key={d.draftId}
                    className="history-item"
                    aria-current={selected ? "true" : undefined}
                  >
                    <button type="button" onClick={() => void select(d.draftId)}>
                      <span>{d.title}</span>
                      <span className="meta">{formatKst(d.createdAt)}</span>
                    </button>
                    {confirming ? (
                      <span className="confirm-inline">
                        <button
                          type="button"
                          className="btn-sm"
                          disabled={deletingId === d.draftId}
                          onClick={() => void confirmDelete(d.draftId)}
                        >
                          확인
                        </button>
                        <button
                          type="button"
                          className="btn-sm"
                          disabled={deletingId === d.draftId}
                          onClick={() => setConfirmingId(null)}
                        >
                          취소
                        </button>
                      </span>
                    ) : (
                      <button type="button" aria-label="삭제" onClick={() => askDelete(d.draftId)}>
                        <Trash2 size={16} aria-hidden="true" />
                      </button>
                    )}
                    {deleteError[d.draftId] && (
                      <div className="callout callout-error" role="alert">
                        <CircleAlert size={16} aria-hidden="true" />
                        <p>{deleteError[d.draftId]}</p>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="history-body">
          {!selectedId && drafts.length > 0 && (
            <p className="meta">왼쪽 목록에서 초안을 선택하세요.</p>
          )}

          {selectedId && bodyLoading && (
            <div className="callout" role="status">
              <Info size={16} aria-hidden="true" />
              <p>초안을 불러오는 중...</p>
            </div>
          )}

          {selectedId && bodyError && (
            <div className="callout callout-error" role="alert">
              <CircleAlert size={16} aria-hidden="true" />
              <p>{bodyError}</p>
            </div>
          )}

          {selectedId && !bodyLoading && body !== null && (
            <div>
              <div className="draft-result-actions">
                <span className="meta">Markdown</span>
                <div>
                  <button type="button" className="btn-sm btn-with-icon" onClick={() => void copy()}>
                    <Copy size={14} aria-hidden="true" />
                    복사
                  </button>
                  <button type="button" className="btn-sm btn-with-icon" onClick={download}>
                    <Download size={14} aria-hidden="true" />
                    다운로드
                  </button>
                  {copied && (
                    <span role="status" className="meta">
                      복사됨
                    </span>
                  )}
                </div>
              </div>
              <pre className="draft-output" aria-label="생성된 마크다운">
                {body}
              </pre>
              {provenance && <ProvenanceSection provenance={provenance} />}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// "사용한 글 재료": segment labels as text badges; source links only on fact segments.
function ProvenanceSection({ provenance }: { provenance: DraftProvenance }) {
  const sources = Array.isArray(provenance.sources) ? provenance.sources : [];
  const segments = Array.isArray(provenance.segments) ? provenance.segments : [];
  const byId = new Map(sources.map((s) => [s.sourceId, s]));
  if (!sources.length && !segments.length) return null;
  return (
    <section className="page-section" aria-label="사용한 글 재료">
      <h3 className="section-title">사용한 글 재료</h3>
      {segments.length > 0 && (
        <ul className="stack">
          {segments.map((seg, i) => (
            <li key={i}>
              <SourceLabel kind={seg.label} showDescription={false} /> <span>{seg.text}</span>
              {seg.label === "fact" &&
                (seg.sourceIds ?? []).map((id) => {
                  const src = byId.get(id);
                  const href = safeExternalUrl(src?.url);
                  if (!src || !href) return null;
                  return (
                    <a key={id} href={href} target="_blank" rel="noopener noreferrer" className="source-meta">
                      {" "}
                      {src.title || src.domain || href}
                    </a>
                  );
                })}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
