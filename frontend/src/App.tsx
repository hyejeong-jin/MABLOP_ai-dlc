import { useEffect, useRef, useState } from "react";
import { Bell, BookOpen, CircleHelp, FileText, LogOut, Moon, PenLine, ShieldCheck, Sun, User } from "lucide-react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginView } from "./views/LoginView";
import { StyleLearningView } from "./views/StyleLearningView";
import { DraftingView } from "./views/DraftingView";
import { ResultView } from "./views/ResultView";
import { ProfileView } from "./views/ProfileView";
import { AdminView } from "./views/AdminView";
import { ManualDialog } from "./components/ManualDialog";
import { LATEST_VERSION, UpdateNotesDialog } from "./components/UpdateNotesDialog";
import { SiteFooter } from "./components/SiteFooter";
import { asset } from "./config";

type View = "style" | "draft" | "result" | "profile" | "admin";
type Dialog = "manual" | "notes" | null;

const TABS: { id: View; label: string; Icon: typeof BookOpen; adminOnly?: boolean }[] = [
  { id: "style", label: "스타일 학습", Icon: BookOpen },
  { id: "draft", label: "초안 작성", Icon: PenLine },
  { id: "result", label: "결과", Icon: FileText },
  { id: "profile", label: "마이페이지", Icon: User },
  { id: "admin", label: "관리", Icon: ShieldCheck, adminOnly: true },
];

const DAY_MS = 24 * 60 * 60 * 1000;
const hideKey = (loginId: string) => `mablop-manual-hidden-until:${loginId}`;
const NOTES_SEEN_KEY = "mablop-notes-seen";

// True while the user's "하루 동안 보지 않기" choice is still in effect.
function manualHiddenToday(loginId: string): boolean {
  const until = Number(localStorage.getItem(hideKey(loginId)) ?? 0);
  return Number.isFinite(until) && until > Date.now();
}

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
  const loginId = user?.loginId ?? "";
  const [view, setView] = useState<View>("style");
  const [welcome] = useState(() => !manualHiddenToday(loginId));
  const [dialog, setDialog] = useState<Dialog>(welcome ? "manual" : null);
  const [notesSeen, setNotesSeen] = useState(() => localStorage.getItem(NOTES_SEEN_KEY) === LATEST_VERSION);
  const [dark, setDark] = useState(() => localStorage.getItem("mablop-theme") === "dark");
  const account = usePopover();
  // Draft just generated; ResultView selects it once, then clears it (Req 23.5).
  const [latestDraftId, setLatestDraftId] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("mablop-theme", dark ? "dark" : "light");
  }, [dark]);

  const isAdmin = user?.role === "admin";
  const tabs = TABS.filter((tab) => !tab.adminOnly || isAdmin);
  const initials = (loginId || "?").slice(0, 2).toUpperCase();
  // The welcome popup is the first-time guide; later opens via "?" show the plain manual.
  const [welcomeOpen, setWelcomeOpen] = useState(welcome);

  function openNotes() {
    localStorage.setItem(NOTES_SEEN_KEY, LATEST_VERSION);
    setNotesSeen(true);
    setDialog("notes");
  }

  function closeManual() {
    setDialog(null);
    setWelcomeOpen(false);
  }

  return (
    <div className="shell">
      <header className="top-bar">
        <div className="brand">
          <img className="brand-mark" src={asset("logo-m.png")} alt="" />
          <div className="brand-text">
            <div className="brand-line">
              <h1 className="wordmark">MABLOP</h1>
              {isAdmin && <span className="admin-chip">관리자 모드</span>}
            </div>
            <p className="app-subtitle">쌓일수록 나다워지는 글쓰기 AI</p>
          </div>
        </div>
        <div className="top-actions">
          <button type="button" className="icon-button has-dot" aria-label="업데이트 노트" onClick={openNotes}>
            <Bell size={18} strokeWidth={1.75} aria-hidden="true" />
            {!notesSeen && <span className="notify-dot" aria-hidden="true" />}
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label={dark ? "일반 모드로 전환" : "다크 모드로 전환"}
            onClick={() => setDark(!dark)}
          >
            {dark ? <Sun size={18} strokeWidth={1.75} aria-hidden="true" /> : <Moon size={18} strokeWidth={1.75} aria-hidden="true" />}
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label="사용자 매뉴얼"
            onClick={() => {
              setWelcomeOpen(false);
              setDialog("manual");
            }}
          >
            <CircleHelp size={18} strokeWidth={1.75} aria-hidden="true" />
          </button>
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
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <main className="view">
        {view === "style" && <StyleLearningView />}
        {view === "draft" && <DraftingView onDraftCreated={setLatestDraftId} />}
        {view === "result" && (
          <ResultView focusDraftId={latestDraftId} onFocusHandled={() => setLatestDraftId(null)} />
        )}
        {view === "profile" && <ProfileView />}
        {view === "admin" && isAdmin && <AdminView />}
      </main>

      <SiteFooter />

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
          aria-label={`${loginId} 계정 메뉴`}
          onClick={() => account.setOpen(!account.open)}
        >
          <span className="avatar" aria-hidden="true">
            {initials}
          </span>
          <span className="account-id">{loginId}</span>
        </button>
      </div>

      {dialog === "manual" && (
        <ManualDialog
          welcome={welcomeOpen}
          onHideToday={() => localStorage.setItem(hideKey(loginId), String(Date.now() + DAY_MS))}
          onClose={closeManual}
        />
      )}
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