// Result / Markdown view (task 11.6). Prop-driven; App.tsx (11.4) owns wiring.
// Single synchronous loading state during generate-draft (Req 2.9). No markdown lib (YAGNI):
// raw Markdown shown in a <pre> with native copy / download controls.
import { useEffect, useState } from "react";

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
      <h1>결과</h1>

      {loading && <p role="status">초안 생성 중...</p>}

      {error && <p role="alert">{error}</p>}

      {!loading && shownMarkdown && (
        <div>
          <div>
            <button type="button" onClick={copy}>
              복사
            </button>
            <button type="button" onClick={download}>
              다운로드 (.md)
            </button>
            {copied && <span role="status">복사됨</span>}
          </div>
          <pre aria-label="생성된 마크다운">{shownMarkdown}</pre>
        </div>
      )}
    </section>
  );
}
