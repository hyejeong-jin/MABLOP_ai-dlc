// Style-learning view (task 11.1). Naver URL + maxPosts, paste fallback, run status + resume.
// Browser -> Lambda only (Req 6.1); no form lib, no state mgr (YAGNI).
import { useState } from "react";
import { postJson, ApiRequestError } from "../api";

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
}

// one paste-fallback row (Req 1.3)
interface PasteRow {
  postId: string;
  text: string;
}

const TOKEN_KEY = "mablop-token";

export function StyleLearningView() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? "");
  const [blogUrl, setBlogUrl] = useState("");
  const [maxPosts, setMaxPosts] = useState(15);
  const [pasteRows, setPasteRows] = useState<PasteRow[]>([]);
  const [resumeRunId, setResumeRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "running">("idle");
  const [result, setResult] = useState<LearnStyleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function updateToken(v: string) {
    setToken(v);
    localStorage.setItem(TOKEN_KEY, v); // persist pre-shared token locally
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
        {
          action: "learn-style",
          blogUrl,
          maxPosts,
          ...(pastedTexts.length ? { pastedTexts } : {}),
          resumeRunId,
        },
        token,
      );
      setResult(res);
      // in-progress -> keep runId so the user can resume (Req 1.9)
      setResumeRunId(res.status === "in-progress" ? res.runId : null);
    } catch (err) {
      const msg =
        err instanceof ApiRequestError ? `${err.code}: ${err.message}` : String(err);
      setError(msg);
    } finally {
      setStatus("idle");
    }
  }

  return (
    <section>
      <h1>스타일 학습</h1>
      <form onSubmit={submit}>
        <label>
          토큰
          <input
            type="password"
            value={token}
            onChange={(e) => updateToken(e.target.value)}
          />
        </label>
        <label>
          네이버 블로그 URL
          <input
            type="url"
            value={blogUrl}
            onChange={(e) => setBlogUrl(e.target.value)}
            placeholder="https://blog.naver.com/<id>"
          />
        </label>
        <label>
          최대 포스트 수
          <input
            type="number"
            min={1}
            value={maxPosts}
            onChange={(e) => setMaxPosts(Number(e.target.value))}
          />
        </label>

        <fieldset>
          <legend>붙여넣기 대체 입력</legend>
          {pasteRows.map((row, i) => (
            <div key={i}>
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
          <button type="button" onClick={addPasteRow}>
            붙여넣기 행 추가
          </button>
        </fieldset>

        <button type="submit" disabled={status === "running"}>
          {resumeRunId ? "이어서 학습" : "학습 시작"}
        </button>
      </form>

      {status === "running" && <p>학습 중...</p>}
      {error && <p role="alert">{error}</p>}

      {result && (
        <div>
          <p>상태: {result.status}</p>
          <p>runId: {result.runId}</p>
          {result.status === "in-progress" && (
            <p>진행 중입니다. 다시 제출하면 이어서 학습합니다.</p>
          )}
          {result.status === "completed" && (
            <p>
              처리된 포스트: {result.processedPosts ?? 0}, 청크: {result.chunks ?? 0}
            </p>
          )}
          {result.failedPosts && result.failedPosts.length > 0 && (
            <div>
              <p>크롤링 실패 (붙여넣기 필요):</p>
              <ul>
                {result.failedPosts.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
