// Result / Markdown view (task 11.6). Prop-driven; App.tsx (11.4) owns wiring.
// Single synchronous loading state during generate-draft (Req 2.9). No markdown lib (YAGNI):
// raw Markdown shown in a <pre> with native copy / download controls.
import { useState } from "react";

export interface ResultViewProps {
  loading?: boolean; // synchronous wait during generate-draft; defaults false until 11.4 wires it
  markdown?: string; // returned Draft (Markdown)
  error?: string;
}

export function ResultView({ loading = false, markdown, error }: ResultViewProps) {
  const [copied, setCopied] = useState(false);

  // native clipboard copy; no deps
  async function copy() {
    if (!markdown) return;
    await navigator.clipboard.writeText(markdown);
    setCopied(true);
  }

  // download raw markdown as .md via blob url
  function download() {
    if (!markdown) return;
    const blob = new Blob([markdown], { type: "text/markdown" });
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

      {!loading && markdown && (
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
          <pre aria-label="생성된 마크다운">{markdown}</pre>
        </div>
      )}
    </section>
  );
}
