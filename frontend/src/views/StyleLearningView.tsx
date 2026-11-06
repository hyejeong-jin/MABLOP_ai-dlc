// Style-learning view (task 11.1). Naver URL + maxPosts, paste fallback, run status + resume.
// Browser -> Lambda only (Req 6.1); no form lib, no state mgr (YAGNI).
import { useEffect, useState } from "react";
import { CircleAlert, Info } from "lucide-react";
import { postJson, ApiRequestError } from "../api";
import { formatKst } from "../format";
import { GrantSelect } from "../grants/GrantSelect";
import { useGrants } from "../grants/useGrants";

// learn-style response (design: Interfaces / learn-style).
interface LearnStyleResponse {
  runId: string;
  status: "completed" | "in-progress";
  processedPosts?: number;
  chunks?: number;
  styleProfileKey?: string;
  indexKey?: string;
  checkpoint?: unknown;
  failedPosts?: string[]; // post ids whose crawl failed -> paste fallback
  learningStatus?: LearningStatus;
}

// one paste-fallback row (Req 1.3)

interface LearnedPost {
  postId: string;
  chunkCount: number;
}

interface LearningStatus {
  runId?: string;
  updatedAt?: string;
  processedPosts: number;
  chunks: number;
  posts: LearnedPost[];
}

interface PasteRow {
  postId: string;
  text: string;
}

interface PreviewPost {
  postId: string;
  url?: string;
  title?: string;
  hasRssFallback?: boolean;
}

interface PreviewResponse {
  count: number;
  posts: PreviewPost[];
}


export function StyleLearningView() {
  const grants = useGrants();
  const [blogUrl, setBlogUrl] = useState("");
  const [maxPosts, setMaxPosts] = useState(15);
  const [pasteRows, setPasteRows] = useState<PasteRow[]>([]);
  const [resumeRunId, setResumeRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "running">("idle");
  const [result, setResult] = useState<LearnStyleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [learningStatus, setLearningStatus] = useState<LearningStatus | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  function loadLearningStatus() {
    postJson<LearningStatus>({ action: "get-learning-status" })
      .then(setLearningStatus)
      .catch(() => undefined);
  }

  useEffect(() => {
    loadLearningStatus();
  }, []);

  async function previewSources() {
    if (!blogUrl) {
      setPreviewError("네이버 블로그 URL을 먼저 입력하세요.");
      return;
    }
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      // preview-learning-sources is not a Bedrock action: no grantId.
      const data = await postJson<PreviewResponse>({ action: "preview-learning-sources", blogUrl, maxPosts });
      setPreview(data);
    } catch (err) {
      setPreviewError(err instanceof ApiRequestError ? `${err.code}: ${err.message}` : String(err));
    } finally {
      setPreviewLoading(false);
    }
  }

  function addPasteRow() {
    setPasteRows((rows) => [...rows, { postId: "", text: "" }]);
  }

  function updatePasteRow(i: number, patch: Partial<PasteRow>) {
    setPasteRows((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("running");
    setError(null);
    // only send non-empty paste rows
    const pastedTexts = pasteRows.filter((r) => r.postId.trim() && r.text.trim());
    try {
      const res = await postJson<LearnStyleResponse>(
        grants.withGrant({
          action: "learn-style",
          blogUrl,
          maxPosts,
          ...(pastedTexts.length ? { pastedTexts } : {}),
          resumeRunId,
        }),
      );
      setResult(res);
      if (res.learningStatus) setLearningStatus(res.learningStatus);
      // in-progress -> keep runId so the user can resume (Req 1.9)
      setResumeRunId(res.status === "in-progress" ? res.runId : null);
    } catch (err) {
      // 403 forbidden: grant options are reloaded; show the backend message.
      if (grants.handleGrantError(err)) {
        setError(err instanceof ApiRequestError ? err.message : String(err));
        return;
      }
      const msg = err instanceof ApiRequestError && err.code === "bad_request"
        ? "학습할 포스트가 없습니다. 붙여넣기 행을 추가하고 postId와 본문을 입력해주세요."
        : err instanceof ApiRequestError ? `${err.code}: ${err.message}` : String(err);
      setError(msg);
    } finally {
      setStatus("idle");
    }
  }

  return (
    <section>
      <h2 className="page-title">스타일 학습</h2>
      <p className="page-lead">내 네이버 블로그 글을 읽어 문체를 익힙니다. 학습한 글이 쌓일수록 초안이 내 말투에 가까워져요.</p>

      {learningStatus && learningStatus.posts.length > 0 && (
        <section className="card" aria-label="학습 완료된 포스트">
          <h3 className="section-title">현재 작업공간 학습 완료 목록</h3>
          <p className="meta mono">포스트 {learningStatus.processedPosts}개 · 청크 {learningStatus.chunks}개</p>
          <ul className="plain-list">
            {learningStatus.posts.map((post) => (
              <li key={post.postId}>
                <strong className="id">{post.postId}</strong>
                <span className="sub mono">({post.chunkCount} chunks)</span>
              </li>
            ))}
          </ul>
          <div className="preview-actions">
            <button type="button" className="btn-sm" onClick={loadLearningStatus}>학습 목록 새로고침</button>
            {learningStatus.updatedAt && <small className="meta">최근 학습: {formatKst(learningStatus.updatedAt)}</small>}
          </div>
        </section>
      )}

      <form className="stack page-section" onSubmit={submit}>
        <GrantSelect grants={grants} />
        <label className="field">
          네이버 블로그 URL
          <input
            type="url"
            value={blogUrl}
            onChange={(e) => setBlogUrl(e.target.value)}
            placeholder="https://blog.naver.com/<id>"
          />
        </label>
        <label className="field">
          최대 포스트 수
          <input
            type="number"
            min={1}
            value={maxPosts}
            onChange={(e) => setMaxPosts(Number(e.target.value))}
          />
        </label>

        <div className="preview-actions">
          <button type="button" onClick={previewSources} disabled={previewLoading || !blogUrl}>
            {previewLoading ? "공개 포스트 확인 중..." : "공개 포스트 미리보기"}
          </button>
          <span className="meta">학습 전에 공개 RSS에서 발견되는 포스트를 확인합니다.</span>
        </div>
        {previewError && (
          <div className="callout callout-error" role="alert">
            <CircleAlert size={16} aria-hidden="true" />
            <p>{previewError}</p>
          </div>
        )}
        {preview && (
          <fieldset className="source-preview">
            <legend>발견된 공개 포스트 {preview.count}개</legend>
            {preview.posts.length === 0 && <p className="field-hint">발견된 포스트가 없습니다. 아래 붙여넣기 입력을 사용하세요.</p>}
            <ul className="plain-list">
              {preview.posts.map((post) => (
                <li key={post.postId}>
                  <span>
                    <strong className="id">{post.postId}</strong>
                    {post.title && <span> · {post.title}</span>}
                  </span>
                  {post.hasRssFallback && <small className="sub">RSS 본문 사용 가능</small>}
                </li>
              ))}
            </ul>
          </fieldset>
        )}

        <fieldset>
          <legend>붙여넣기 대체 입력</legend>
          <p className="field-hint">자동 수집이 실패하면 행을 추가하고 고유한 postId와 포스트 본문을 붙여넣으세요.</p>
          {pasteRows.map((row, i) => (
            <div className="paste-row" key={i}>
              <input
                aria-label={`postId ${i}`}
                value={row.postId}
                onChange={(e) => updatePasteRow(i, { postId: e.target.value })}
                placeholder="postId"
              />
              <textarea
                aria-label={`pasted text ${i}`}
                value={row.text}
                onChange={(e) => updatePasteRow(i, { text: e.target.value })}
                placeholder="크롤링 실패 시 본문 붙여넣기"
              />
            </div>
          ))}
          <div>
            <button type="button" className="btn-sm" onClick={addPasteRow}>
              붙여넣기 행 추가
            </button>
          </div>
        </fieldset>

        <button type="submit" className="btn-primary btn-block" disabled={status === "running" || !grants.canCallBedrock}>
          {resumeRunId ? "이어서 학습" : "학습 시작"}
        </button>
      </form>

      {status === "running" && (
        <div className="callout page-section" role="status">
          <Info size={16} aria-hidden="true" />
          <p>학습 중...</p>
        </div>
      )}
      {error && (
        <div className="callout callout-error page-section" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <p>{error}</p>
        </div>
      )}

      {result && (
        <div className="card page-section">
          <div className="result-meta">
            <p>상태: {result.status}</p>
            <p>runId: {result.runId}</p>
            {result.status === "in-progress" && (
              <p>진행 중입니다. 다시 제출하면 이어서 학습합니다.</p>
            )}
            {result.status === "completed" && (
              <p className="mono">
                처리된 포스트: {result.processedPosts ?? 0}, 청크: {result.chunks ?? 0}
              </p>
            )}
          </div>
          {result.failedPosts && result.failedPosts.length > 0 && (
            <div>
              <h3 className="section-title page-section">수집하지 못한 포스트 (본문 붙여넣기 필요)</h3>
              <ul className="plain-list">
                {result.failedPosts.map((id) => (
                  <li key={id}>
                    <span className="id">{id}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}