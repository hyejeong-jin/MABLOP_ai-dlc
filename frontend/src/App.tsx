import { useState } from "react";
import { BookOpen, FileText, PenLine, ShieldCheck, User } from "lucide-react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginView } from "./views/LoginView";
import { StyleLearningView } from "./views/StyleLearningView";
import { DraftingView } from "./views/DraftingView";
import { ResultView } from "./views/ResultView";
import { ProfileView } from "./views/ProfileView";
import { AdminView } from "./views/AdminView";

type View = "style" | "draft" | "result" | "profile" | "admin";

const TABS: { id: View; label: string; Icon: typeof BookOpen; adminOnly?: boolean }[] = [
  { id: "style", label: "스타일 학습", Icon: BookOpen },
  { id: "draft", label: "초안 작성", Icon: PenLine },
  { id: "result", label: "결과", Icon: FileText },
  { id: "profile", label: "내 정보", Icon: User },
  { id: "admin", label: "관리", Icon: ShieldCheck, adminOnly: true },
];

function Shell() {
  const { user } = useAuth();
  const [view, setView] = useState<View>("style");
  // Draft just generated; ResultView selects it once, then clears it (Req 23.5).
  const [latestDraftId, setLatestDraftId] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";
  const tabs = TABS.filter((tab) => !tab.adminOnly || isAdmin);

  return (
    <main className="shell">
      <header className="app-header brand">
        <img className="brand-mark" src={`${import.meta.env.BASE_URL}logo-mark.png`} alt="" width="44" height="26" />
        <div className="brand-text">
          <h1 className="wordmark">MABLOP</h1>
          <p className="app-subtitle">내 블로그 문체로 쓰는 초안 도구</p>
        </div>
      </header>
      <nav className="tabs" aria-label="주요 기능">
        {tabs.map(({ id, label, Icon }) => (
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
      {view === "draft" && <DraftingView onDraftCreated={setLatestDraftId} />}
      {view === "result" && (
        <ResultView
          focusDraftId={latestDraftId}
          onFocusHandled={() => setLatestDraftId(null)}
        />
      )}
      {view === "profile" && <ProfileView />}
      {view === "admin" && isAdmin && <AdminView />}
    </main>
  );
}

function Gate() {
  const { user } = useAuth();
  return user ? <Shell /> : <LoginView />;
}

export function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
