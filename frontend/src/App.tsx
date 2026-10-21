import { useState } from "react";
import { StyleLearningView } from "./views/StyleLearningView";
import { DraftingView } from "./views/DraftingView";
import { ResultView } from "./views/ResultView";

type View = "style" | "draft" | "result";

export function App() {
  const [view, setView] = useState<View>("style");
  return (
    <main>
      <header className="app-brand">
        <div className="brand-mark" aria-hidden="true">M</div>
        <div>
          <h1>MABLOP</h1>
          <p className="app-subtitle">개인 맞춤형 AI 블로그 작성 도구</p>
        </div>
      </header>
      <nav aria-label="주요 기능">
        <button className={view === "style" ? "tab-button active" : "tab-button"} onClick={() => setView("style")}>✦ 스타일 학습</button>
        <button className={view === "draft" ? "tab-button active" : "tab-button"} onClick={() => setView("draft")}>✎ 초안 작성</button>
        <button className={view === "result" ? "tab-button active" : "tab-button"} onClick={() => setView("result")}>▤ 결과</button>
      </nav>
      {view === "style" && <StyleLearningView />}
      {view === "draft" && <DraftingView />}
      {view === "result" && <ResultView />}
    </main>
  );
}
