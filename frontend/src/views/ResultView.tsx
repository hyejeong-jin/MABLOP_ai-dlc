// Result / Markdown view (task 11.6). Prop-driven; App.tsx (11.4) owns wiring.
// Single synchronous loading state during generate-draft (Req 2.9). No markdown lib (YAGNI):
// raw Markdown shown in a <pre> with native copy / download controls.
import { useEffect, useState } from "react";
import { CircleAlert, Copy, Download, Info } from "lucide-react";

const LAST_DRAFT_KEY = "mablop-last-draft";

export interface ResultViewProps {
  loading?: boolean; // synchronous wait during generate-draft; defaults false until 11.4 wires it
  markdown?: string; // returned Draft (Markdown)
  error?: string;
}

export function ResultView({ loading = false, markdown, error }: ResultViewProps) {
  const [copied, setCopied] = useState(false);
  const [storedMarkdown, setStoredMarkdown] = useState(
    () => localStorage.getItem(LAST_DRAFT_KEY) ?? "",
  );
  const shownMarkdown = markdown ?? storedMarkdown;

  useEffect(() => {
    if (markdown !== undefined) {
      setStoredMarkdown(markdown);
      localStorage.setItem(LAST_DRAFT_KEY, markdown);
    }
  }, [markdown]);

  // native clipboard copy; no deps
  async function copy() {
    if (!shownMarkdown) return;
    await navigator.clipboard.writeText(shownMarkdown);
    setCopied(true);
  }

  // download raw markdown as .md via blob url
  function download() {
    if (!shownMarkdown) return;
    const blob = new Blob([shownMarkdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "draft.md";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section>
      <h2 className="page-title">결과</h2>
      <p className="page-lead">가장 최근에 만든 초안입니다. 이 브라우저에 저장돼 있어서 새로고침해도 남아 있어요.</p>

      {loading && (
        <div className="callout" role="status">
          <Info size={16} aria-hidden="true" />
          <p>초안 생성 중...</p>
        </div>
      )}

      {error && (
        <div className="callout callout-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <p>{error}</p>
        </div>
      )}

      {!loading && !shownMarkdown && !error && (
        <p className="meta">아직 만든 초안이 없습니다. 초안 작성 탭에서 먼저 만들어 보세요.</p>
      )}

      {!loading && shownMarkdown && (
        <div>
          <div className="draft-result-actions">
            <span className="meta">Markdown</span>
            <div>
              <button type="button" className="btn-sm btn-with-icon" onClick={copy}>
                <Copy size={14} aria-hidden="true" />
                복사
              </button>
              <button type="button" className="btn-sm btn-with-icon" onClick={download}>
                <Download size={14} aria-hidden="true" />
                다운로드 (.md)
              </button>
              {copied && <span role="status" className="meta">복사됨</span>}
            </div>
          </div>
          <pre className="draft-output" aria-label="생성된 마크다운">{shownMarkdown}</pre>
        </div>
      )}
    </section>
  );
}