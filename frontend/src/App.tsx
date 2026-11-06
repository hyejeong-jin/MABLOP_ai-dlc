import { useState } from "react";
import { BookOpen, FileText, Gauge, LogOut, Megaphone, PenLine, ShieldCheck, User } from "lucide-react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginView } from "./views/LoginView";
import { StyleLearningView } from "./views/StyleLearningView";
import { DraftingView } from "./views/DraftingView";
import { ResultView } from "./views/ResultView";
import { ProfileView } from "./views/ProfileView";
import { AdminView } from "./views/AdminView";
import { UsageDialog } from "./components/UsageDialog";
import { ManualDialog } from "./components/ManualDialog";
import { UpdateNotesDialog } from "./components/UpdateNotesDialog";

type View = "style" | "draft" | "result" | "profile" | "admin";
type Dialog = "usage" | "manual" | "notes" | null;

const TABS: { id: View; label: string; Icon: typeof BookOpen; adminOnly?: boolean }[] = [
  { id: "style", label: "스타일 학습", Icon: BookOpen },
  { id: "draft", label: "초안 작성", Icon: PenLine },
  { id: "result", label: "결과", Icon: FileText },
  { id: "profile", label: "내 정보", Icon: User },
  { id: "admin", label: "관리", Icon: ShieldCheck, adminOnly: true },
];

function Shell() {
  const { user, logout } = useAuth();
  const [view, setView] = useState<View>("style");
  const [dialog, setDialog] = useState<Dialog>(null);
  // Draft just generated; ResultView selects it once, then clears it (Req 23.5).
  const [latestDraftId, setLatestDraftId] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";
  const tabs = TABS.filter((tab) => !tab.adminOnly || isAdmin);
  const year = new Date().getFullYear();

  return (
    <div className={isAdmin ? "layout is-admin" : "layout"}>
      <aside className="sidebar">
        <header className="brand">
          <img className="brand-mark" src={`${import.meta.env.BASE_URL}logo-m.png`} alt="" />
          <div className="brand-text">
            <h1 className="wordmark">MABLOP</h1>
            <p className="app-subtitle">쌓일수록 나다워지는 글쓰기 AI</p>
          </div>
        </header>

        <div className="space">
          <span className="space-label">
            <strong>{user?.loginId}</strong>님의 공간
          </span>
          {isAdmin && <span className="admin-chip">관리자 모드</span>}
        </div>

        <nav className="side-nav" aria-label="주요 기능">
          {tabs.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              className={view === id ? "side-item active" : "side-item"}
              aria-current={view === id ? "page" : undefined}
              onClick={() => setView(id)}
            >
              <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
              {label}
            </button>
          ))}
        </nav>

        <div className="side-bottom">
          <button type="button" className="side-item" onClick={() => setDialog("usage")}>
            <Gauge size={16} strokeWidth={1.75} aria-hidden="true" />내 월 사용량
          </button>
          <button type="button" className="side-item" onClick={() => setDialog("manual")}>
            <BookOpen size={16} strokeWidth={1.75} aria-hidden="true" />
            사용자 매뉴얼
          </button>
          <button type="button" className="side-item" onClick={() => setDialog("notes")}>
            <Megaphone size={16} strokeWidth={1.75} aria-hidden="true" />
            업데이트 노트
          </button>
          <button type="button" className="side-item side-logout" onClick={logout}>
            <LogOut size={16} strokeWidth={1.75} aria-hidden="true" />
            로그아웃
          </button>
          <p className="signature">by hyejeong.jin</p>
        </div>
      </aside>

      <div className="content">
        <main className="content-main">
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
        <footer className="site-footer">
          © {year} MABLOP · Made with care by <strong>hyejeong.jin</strong>
        </footer>
      </div>

      {dialog === "usage" && <UsageDialog onClose={() => setDialog(null)} />}
      {dialog === "manual" && <ManualDialog onClose={() => setDialog(null)} />}
      {dialog === "notes" && <UpdateNotesDialog onClose={() => setDialog(null)} />}
    </div>
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