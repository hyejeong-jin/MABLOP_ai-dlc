// 사용자의 목소리함: 회원이 운영자에게 의견·문의를 남기는 게시판. Browser -> Lambda only.
// 회원: list-voc / create-voc / get-voc / update-voc / archive-voc.
// 관리자: admin-list-voc / admin-get-voc / admin-reply / admin-update-voc-status (작성 폼 없음).
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { CircleAlert } from "lucide-react";
import {
  ApiRequestError,
  VOC_CATEGORY_LABELS,
  VOC_LIMITS,
  VOC_STATUS_LABELS,
  adminGetVoc,
  adminListVoc,
  adminReply,
  adminUpdateVocStatus,
  archiveVoc,
  createVoc,
  getVoc,
  listVoc,
  updateVoc,
  type VocCategory,
  type VocInput,
  type VocRecord,
  type VocReply,
  type VocStatus,
  type VocSummary,
} from "../api";
import { useAuth } from "../auth/AuthContext";
import { VocEditor } from "../components/VocEditor";
import { VocList, type VocRow } from "../components/VocList";
import { formatKst } from "../format";

const msg = (e: unknown) => (e instanceof ApiRequestError ? e.message : "요청을 처리하지 못했습니다.");
const CATEGORIES = Object.keys(VOC_CATEGORY_LABELS) as VocCategory[];
const STATUSES = Object.keys(VOC_STATUS_LABELS) as VocStatus[];

function ReplyView({ reply }: { reply: VocReply }) {
  return (
    <div className="vb-reply">
      <p className="vb-reply-head">
        <strong>운영자 답변</strong>
        <span className="meta">
          {reply.authorLoginId} · {formatKst(reply.updatedAt || reply.createdAt)}
        </span>
      </p>
      <p className="vb-text">{reply.body}</p>
    </div>
  );
}

// 회원이 펼친 본인 글: 본문·답변 조회, 수정, 삭제(보관).
function MemberDetail({ summary, onChanged }: { summary: VocSummary; onChanged: (notice: string) => void }) {
  const [voc, setVoc] = useState<VocRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setVoc(null);
    setError(null);
    getVoc(summary.vocId)
      .then((res) => alive && setVoc(res.voc))
      .catch((e) => alive && setError(msg(e)));
    return () => {
      alive = false;
    };
  }, [summary.vocId, summary.updatedAt]);

  async function save(input: VocInput) {
    setBusy(true);
    setFormError(null);
    try {
      await updateVoc(summary.vocId, input);
      setEditing(false);
      onChanged("문의를 수정했어요.");
    } catch (e) {
      setFormError(msg(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await archiveVoc(summary.vocId);
      setConfirmDelete(false);
      onChanged("문의를 삭제했어요.");
    } catch (e) {
      setError(msg(e));
    } finally {
      setBusy(false);
    }
  }

  if (error && !voc) {
    return (
      <div className="callout callout-error" role="alert">
        <CircleAlert size={16} aria-hidden="true" />
        <p>{error}</p>
      </div>
    );
  }
  if (!voc) {
    return (
      <p className="meta" role="status">
        불러오는 중...
      </p>
    );
  }
  if (editing) {
    return (
      <VocEditor
        initial={voc}
        idPrefix={`voc-edit-${voc.vocId}`}
        busy={busy}
        serverError={formError}
        onSubmit={save}
        onCancel={() => setEditing(false)}
      />
    );
  }
  return (
    <div className="vb-detail-body">
      <p className="vb-text">{voc.body}</p>
      {voc.reply && <ReplyView reply={voc.reply} />}
      {error && (
        <div className="callout callout-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <p>{error}</p>
        </div>
      )}
      {voc.status !== "archived" && (
        <div className="vb-actions">
          {confirmDelete ? (
            <>
              <span className="meta">이 문의를 삭제할까요?</span>
              <button type="button" className="btn-danger" onClick={() => void remove()} disabled={busy}>
                삭제 확인
              </button>
              <button type="button" onClick={() => setConfirmDelete(false)} disabled={busy}>
                취소
              </button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => setEditing(true)}>
                수정
              </button>
              <button type="button" className="btn-danger" onClick={() => setConfirmDelete(true)}>
                삭제
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// 관리자가 펼친 문의: admin-get-voc로 전체 본문과 기존 답변을 불러오고, 답변 입력(수정), 상태 변경.
function AdminDetail({ summary, onChanged }: { summary: VocSummary; onChanged: (notice: string) => void }) {
  const [voc, setVoc] = useState<VocRecord | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reply, setReply] = useState<VocReply | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const hasReply = reply !== null;
  const ownerId = summary.ownerId ?? "";

  useEffect(() => {
    let alive = true;
    setVoc(null);
    setLoadError(null);
    adminGetVoc(ownerId, summary.vocId)
      .then((res) => {
        if (!alive) return;
        setVoc(res.voc);
        setReply(res.voc.reply ?? null);
        setText(res.voc.reply?.body ?? "");
      })
      .catch((e) => alive && setLoadError(msg(e)));
    return () => {
      alive = false;
    };
  }, [ownerId, summary.vocId]);

  async function submitReply(e: FormEvent) {
    e.preventDefault();
    const body = text.trim();
    if (!body) {
      setFieldError("답변을 입력하세요.");
      return;
    }
    if (body.length > VOC_LIMITS.reply) {
      setFieldError(`답변은 ${VOC_LIMITS.reply.toLocaleString()}자 이하로 입력하세요.`);
      return;
    }
    setFieldError(null);
    setBusy(true);
    setError(null);
    try {
      const wasReplied = hasReply;
      const res = await adminReply(ownerId, summary.vocId, body);
      setReply(res.voc.reply ?? null);
      onChanged(wasReplied ? "답변을 수정했어요." : "답변을 등록했어요.");
    } catch (err) {
      setError(msg(err));
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(status: VocStatus) {
    setBusy(true);
    setError(null);
    try {
      await adminUpdateVocStatus(ownerId, summary.vocId, status);
      onChanged("상태를 바꿨어요.");
    } catch (err) {
      setError(msg(err));
    } finally {
      setBusy(false);
    }
  }

  const fieldId = `voc-reply-${summary.vocId}`;
  if (loadError && !voc) {
    return (
      <div className="callout callout-error" role="alert">
        <CircleAlert size={16} aria-hidden="true" />
        <p>{loadError}</p>
      </div>
    );
  }
  if (!voc) {
    return (
      <p className="meta" role="status">
        불러오는 중...
      </p>
    );
  }
  return (
    <div className="vb-detail-body">
      <p className="vb-text">{voc.body}</p>
      {reply && <ReplyView reply={reply} />}
      <form className="vb-form" onSubmit={submitReply} noValidate>
        <div className="field-group">
          <label className="field" htmlFor={fieldId}>
            답변
          </label>
          <textarea
            id={fieldId}
            rows={4}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-invalid={fieldError ? true : undefined}
            aria-describedby={fieldError ? `${fieldId}-error` : undefined}
          />
          {fieldError && (
            <p id={`${fieldId}-error`} className="field-hint" role="alert" style={{ color: "var(--danger)" }}>
              {fieldError}
            </p>
          )}
        </div>
        <div className="vb-actions">
          <button type="submit" className="btn-primary" disabled={busy}>
            {hasReply ? "답변 수정" : "답변 등록"}
          </button>
          <label className="vb-inline-field" htmlFor={`voc-status-${summary.vocId}`}>
            상태 변경
          </label>
          <select
            id={`voc-status-${summary.vocId}`}
            value={summary.status}
            disabled={busy}
            onChange={(e) => void changeStatus(e.target.value as VocStatus)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {VOC_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
      </form>
      {error && (
        <div className="callout callout-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <p>{error}</p>
        </div>
      )}
    </div>
  );
}

export function VoiceBoxView() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [records, setRecords] = useState<VocSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [category, setCategory] = useState<"" | VocCategory>("");
  const [status, setStatus] = useState<"" | VocStatus>("");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [writeOpen, setWriteOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const params = status ? { status } : {};
      const res = isAdmin ? await adminListVoc(params) : await listVoc(params);
      setRecords(res.records ?? []);
    } catch (e) {
      setListError(msg(e));
    } finally {
      setLoading(false);
    }
  }, [status, isAdmin]);

  useEffect(() => {
    void load();
  }, [load]);

  // 번호는 불러온 전체 목록 기준(최신이 가장 큰 번호)이라 필터를 바꿔도 변하지 않는다.
  const rows = useMemo<VocRow[]>(() => {
    const sorted = [...records].sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0));
    const q = query.trim().toLowerCase();
    return sorted
      .map((record, i) => ({ no: sorted.length - i, record }))
      .filter(({ record }) => (!category || record.category === category) && (!q || (record.title ?? "").toLowerCase().includes(q)));
  }, [records, category, query]);

  async function handleCreate(input: VocInput) {
    setSaving(true);
    setFormError(null);
    setNotice(null);
    try {
      await createVoc(input);
      setWriteOpen(false);
      setNotice("문의를 남겼어요.");
      await load();
    } catch (e) {
      setFormError(msg(e));
    } finally {
      setSaving(false);
    }
  }

  function handleChanged(text: string) {
    setNotice(text);
    void load();
  }

  function toggle(vocId: string) {
    setExpandedId((cur) => (cur === vocId ? null : vocId));
  }

  function search(e: FormEvent) {
    e.preventDefault();
    setQuery(queryInput);
  }

  return (
    <section className="feature-stack vb" aria-labelledby="voicebox-title">
      <div className="vb-head">
        <div>
          <h2 id="voicebox-title" className="page-title section-accent">
            사용자의 목소리함
          </h2>
          <p className="page-lead">
            {isAdmin
              ? "회원들이 남긴 문의에 답변할 수 있어요."
              : "MABLOP 서비스에 대한 의견이나 문의를 운영자에게 남겨 주세요. 남긴 글은 운영자와 나만 볼 수 있어요."}
          </p>
        </div>
        {!isAdmin && !writeOpen && (
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              setWriteOpen(true);
              setFormError(null);
              setNotice(null);
            }}
          >
            문의 남기기
          </button>
        )}
      </div>

      {!isAdmin && writeOpen && (
        <div className="feature-card">
          <h3 className="section-title">문의 남기기</h3>
          <VocEditor
            idPrefix="voc-new"
            busy={saving}
            serverError={formError}
            onSubmit={handleCreate}
            onCancel={() => setWriteOpen(false)}
          />
        </div>
      )}

      {notice && (
        <div className="callout" role="status">
          <p>{notice}</p>
        </div>
      )}

      <div className="feature-card">
        <form className="vb-filters" onSubmit={search} role="search" aria-label="문의 검색">
          <select aria-label="분류 필터" value={category} onChange={(e) => setCategory(e.target.value as "" | VocCategory)}>
            <option value="">분류 전체</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {VOC_CATEGORY_LABELS[c]}
              </option>
            ))}
          </select>
          <select aria-label="상태 필터" value={status} onChange={(e) => setStatus(e.target.value as "" | VocStatus)}>
            <option value="">상태 전체</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {VOC_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
          <input
            type="search"
            aria-label="제목 검색"
            placeholder="제목으로 검색"
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
          />
          <button type="submit">검색</button>
        </form>

        {listError && (
          <div className="callout callout-error" role="alert">
            <CircleAlert size={16} aria-hidden="true" />
            <p>{listError}</p>
          </div>
        )}
        {loading && records.length === 0 ? (
          <p className="meta" role="status">
            불러오는 중...
          </p>
        ) : rows.length === 0 ? (
          <div className="empty-state" role="status">
            <p>{records.length === 0 && !category && !query ? (isAdmin ? "아직 접수된 문의가 없어요." : "아직 남긴 문의가 없어요.") : "조건에 맞는 문의가 없어요."}</p>
          </div>
        ) : (
          <VocList
            rows={rows}
            showOwner={isAdmin}
            expandedId={expandedId}
            onToggle={toggle}
            renderDetail={(r) =>
              isAdmin ? (
                <AdminDetail summary={r} onChanged={handleChanged} />
              ) : (
                <MemberDetail summary={r} onChanged={handleChanged} />
              )
            }
          />
        )}
      </div>
    </section>
  );
}
