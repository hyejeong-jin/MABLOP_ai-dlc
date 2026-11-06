import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  CircleHelp,
  FileText,
  LogOut,
  Megaphone,
  PenLine,
  ShieldCheck,
  User,
} from "lucide-react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginView } from "./views/LoginView";
import { StyleLearningView } from "./views/StyleLearningView";
import { DraftingView } from "./views/DraftingView";
import { ResultView } from "./views/ResultView";
import { ProfileView } from "./views/ProfileView";
import { AdminView } from "./views/AdminView";
import { ManualDialog } from "./components/ManualDialog";
import { UpdateNotesDialog } from "./components/UpdateNotesDialog";

type View = "style" | "draft" | "result" | "profile" | "admin";
type Dialog = "manual" | "notes" | null;

const TABS: { id: View; label: string; Icon: typeof BookOpen; adminOnly?: boolean }[] = [
  { id: "style", label: "스타일 학습", Icon: BookOpen },
  { id: "draft", label: "초안 작성", Icon: PenLine },
  { id: "result", label: "결과", Icon: FileText },
  { id: "profile", label: "마이페이지", Icon: User },
  { id: "admin", label: "관리", Icon: ShieldCheck, adminOnly: true },
];

// Closes a popover when the user clicks outside of it or presses Escape.
function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return { open, setOpen, ref };
}

function Shell() {
  const { user, logout } = useAuth();
  const [view, setView] = useState<View>("style");
  const [dialog, setDialog] = useState<Dialog>(null);
  const help = usePopover();
  const account = usePopover();
  // Draft just generated; ResultView selects it once, then clears it (Req 23.5).
  const [latestDraftId, setLatestDraftId] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";
  const tabs = TABS.filter((tab) => !tab.adminOnly || isAdmin);
  const year = new Date().getFullYear();
  const initials = (user?.loginId ?? "?").slice(0, 2).toUpperCase();

  function openDialog(next: Dialog) {
    help.setOpen(false);
    setDialog(next);
  }

  return (
    <div className="shell">
      <header className="app-header brand top-bar">
        <img className="brand-mark" src={`${import.meta.env.BASE_URL}logo-m.png`} alt="" />
        <div className="brand-text">
          <h1 className="wordmark">MABLOP</h1>
          <p className="app-subtitle">쌓일수록 나다워지는 글쓰기 AI</p>
        </div>
        {isAdmin && <span className="admin-chip">관리자 모드</span>}
        <div className="top-actions popover-host" ref={help.ref}>
          <button
            type="button"
            className="icon-button"
            aria-label="도움말과 소식"
            aria-haspopup="menu"
            aria-expanded={help.open}
            onClick={() => help.setOpen(!help.open)}
          >
            <CircleHelp size={20} strokeWidth={1.75} aria-hidden="true" />
          </button>
          {help.open && (
            <div className="popover popover-right" role="menu">
              <button type="button" role="menuitem" className="menu-item" onClick={() => openDialog("manual")}>
                <BookOpen size={16} strokeWidth={1.75} aria-hidden="true" />
                사용자 매뉴얼
              </button>
              <button type="button" role="menuitem" className="menu-item" onClick={() => openDialog("notes")}>
                <Megaphone size={16} strokeWidth={1.75} aria-hidden="true" />
                업데이트 노트
              </button>
            </div>
          )}
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

      <footer className="site-footer">
        © {year} MABLOP · Made with care by <strong>hyejeong.jin</strong>
      </footer>

      <div className="account-dock popover-host" ref={account.ref}>
        {account.open && (
          <div className="popover popover-up" role="menu">
            <button
              type="button"
              role="menuitem"
              className="menu-item"
              onClick={() => {
                account.setOpen(false);
                setView("profile");
              }}
            >
              <User size={16} strokeWidth={1.75} aria-hidden="true" />
              마이페이지
            </button>
            <button type="button" role="menuitem" className="menu-item" onClick={logout}>
              <LogOut size={16} strokeWidth={1.75} aria-hidden="true" />
              로그아웃
            </button>
          </div>
        )}
        <button
          type="button"
          className="account-chip"
          aria-haspopup="menu"
          aria-expanded={account.open}
          aria-label={`${user?.loginId} 계정 메뉴`}
          onClick={() => account.setOpen(!account.open)}
        >
          <span className="avatar" aria-hidden="true">
            {initials}
          </span>
          <span className="account-id">{user?.loginId}</span>
        </button>
      </div>

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