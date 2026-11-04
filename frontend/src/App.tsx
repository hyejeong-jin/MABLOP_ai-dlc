import { useState } from "react";
import { BookOpen, FileText, PenLine } from "lucide-react";
import { StyleLearningView } from "./views/StyleLearningView";
import { DraftingView } from "./views/DraftingView";
import { ResultView } from "./views/ResultView";
type View = "style" | "draft" | "result";

const TABS: { id: View; label: string; Icon: typeof BookOpen }[] = [
  { id: "style", label: "스타일 학습", Icon: BookOpen },
  { id: "draft", label: "초안 작성", Icon: PenLine },
  { id: "result", label: "결과", Icon: FileText },
];

export function App() {
  const [view, setView] = useState<View>("style");
  return (
    <main className="shell">
      <header className="app-header">
        <h1 className="wordmark">MABLOP</h1>
        <p className="app-subtitle">내 블로그 문체로 쓰는 초안 도구</p>
      </header>
      <nav className="tabs" aria-label="주요 기능">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            className={view === id ? "tab active" : "tab"}
            aria-current={view === id ? "page" : undefined}
            onClick={() => setView(id)}
          >
            <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>
      {view === "style" && <StyleLearningView />}
      {view === "draft" && <DraftingView />}
      {view === "result" && <ResultView />}
    </main>
  );
}