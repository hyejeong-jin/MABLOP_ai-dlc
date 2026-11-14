// 사용자의 목소리함: VOC create/list/get/update/archive. Browser -> Lambda only.
import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleAlert } from "lucide-react";
import {
  ApiRequestError,
  VOC_STATUS_LABELS,
  archiveVoc,
  createVoc,
  getVoc,
  listVoc,
  updateVoc,
  type VocInput,
  type VocRecord,
  type VocStatus,
  type VocSummary,
} from "../api";
import { VocEditor } from "../components/VocEditor";
import { VocList } from "../components/VocList";
import { formatKstDate } from "../format";

const msg = (e: unknown) => (e instanceof ApiRequestError ? e.message : "요청을 처리하지 못했습니다.");

export function VoiceBoxView() {
  const [records, setRecords] = useState<VocSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [status, setStatus] = useState<"" | VocStatus>("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<VocRecord | null>(null);
  const [editorOpen, setEditorOpen] = useState(true);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const res = await listVoc(status ? { status } : {});
      setRecords(res.records);
    } catch (e) {
      setListError(msg(e));
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  // Period and search are applied on the list index fields (createdAt, preview).
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return records.filter((r) => {
      const day = formatKstDate(r.createdAt);
      if (from && day < from) return false;
      if (to && day > to) return false;
      if (q && !r.preview.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [records, from, to, query]);

  async function handleSubmit(input: VocInput) {
    setSaving(true);
    setFormError(null);
    setNotice(null);
    try {
      if (editing) {
        await updateVoc(editing.vocId, input);
        setNotice("의견을 수정했습니다.");
      } else {
        await createVoc(input);
        setNotice("의견을 저장했습니다.");
      }
      setEditing(null);
      await load();
    } catch (e) {
      setFormError(msg(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleEdit(vocId: string) {
    setBusyId(vocId);
    setFormError(null);
    setNotice(null);
    try {
      const res = await getVoc(vocId);
      setEditing(res.voc);
      setEditorOpen(true);
    } catch (e) {
      setListError(msg(e));
    } finally {
      setBusyId(null);
    }
  }

  async function handleArchive(vocId: string) {
    setBusyId(vocId);
    setListError(null);
    setNotice(null);
    try {
      await archiveVoc(vocId);
      if (editing?.vocId === vocId) setEditing(null);
      setNotice("의견을 보관했습니다.");
      await load();
    } catch (e) {
      setListError(msg(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="feature-stack" aria-labelledby="voicebox-title">
      <div>
        <h2 id="voicebox-title" className="page-title section-accent">
          사용자의 목소리함
        </h2>
        <p className="page-lead">고객의 말과 반응을 모아 두고 콘텐츠 아이디어로 활용하세요.</p>
      </div>

      <div className="feature-card">
        <h3 className="section-title">{editing ? "의견 수정" : "새 의견 등록"}</h3>
        {editorOpen && (
          <VocEditor
            initial={editing}
            busy={saving}
            serverError={formError}
            onSubmit={handleSubmit}
            onCancel={editing ? () => setEditing(null) : undefined}
          />
        )}
      </div>

      {notice && (
        <div className="callout" role="status">
          <p>{notice}</p>
        </div>
      )}

      <div className="feature-card">
        <h3 className="section-title">의견 목록</h3>
        <div className="feature-grid" role="group" aria-label="목록 필터">
          <div className="field-group">
            <label className="field" htmlFor="voc-filter-status">
              상태
              <select
                id="voc-filter-status"
                value={status}
                onChange={(e) => setStatus(e.target.value as "" | VocStatus)}
              >
                <option value="">전체</option>
                {(Object.keys(VOC_STATUS_LABELS) as VocStatus[]).map((s) => (
                  <option key={s} value={s}>
                    {VOC_STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="field-group">
            <label className="field" htmlFor="voc-filter-query">
              검색
              <input
                id="voc-filter-query"
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-describedby="voc-filter-query-hint"
              />
            </label>
            <p id="voc-filter-query-hint" className="field-hint">
              원문 앞부분에서 찾습니다.
            </p>
          </div>
          <div className="field-group">
            <label className="field" htmlFor="voc-filter-from">
              시작일
              <input id="voc-filter-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </label>
          </div>
          <div className="field-group">
            <label className="field" htmlFor="voc-filter-to">
              종료일
              <input id="voc-filter-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
          </div>
        </div>

        {listError && (
          <div className="callout callout-error" role="alert">
            <CircleAlert size={16} aria-hidden="true" />
            <p>{listError}</p>
          </div>
        )}
        {loading ? (
          <p className="meta" role="status">
            불러오는 중...
          </p>
        ) : (
          <VocList records={visible} busyId={busyId} onEdit={handleEdit} onArchive={handleArchive} />
        )}
      </div>
    </section>
  );
}
