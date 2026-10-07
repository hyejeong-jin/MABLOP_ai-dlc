import { useState } from "react";
import { StyleLearningView } from "./views/StyleLearningView";
import { DraftingView } from "./views/DraftingView";
import { ResultView } from "./views/ResultView";

// 3 views toggled by local state (YAGNI: no router).
type View = "style" | "draft" | "result";

export function App() {
  const [view, setView] = useState<View>("style");

  return (
    <main>
      <h1>MABLOP</h1>
      <nav>
        <button onClick={() => setView("style")}>스타일 학습</button>
        <button onClick={() => setView("draft")}>초안 작성</button>
        <button onClick={() => setView("result")}>결과</button>
      </nav>
      {view === "style" && <StyleLearningView />}
      {view === "draft" && <DraftingView />}
      {view === "result" && <ResultView />}
    </main>
  );
}
