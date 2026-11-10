// Login and registration screen (Req 13.1-13.4, 13.10-13.12, 16.1, 16.2, 16.4, 16.6).
// One centered column with two modes. No navigation is shown here.
import { useState, type FormEvent } from "react";
import { CircleAlert, Info } from "lucide-react";

import { ApiRequestError } from "../api";
import { useAuth } from "../auth/AuthContext";
import { CapsLockHint, useCapsLock } from "../components/CapsLock";
import { PasswordHelpDialog } from "../components/PasswordHelpDialog";
import { SiteFooter } from "../components/SiteFooter";

type Mode = "login" | "register";

const MISMATCH_MESSAGE = "비밀번호가 일치하지 않습니다.";
const FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.";

function errorMessage(err: unknown): string {
  if (err instanceof ApiRequestError && err.message) return err.message;
  return FALLBACK_MESSAGE;
}

export function LoginView() {
  const { login, register, notice } = useAuth();

  const [mode, setMode] = useState<Mode>("login");
  const [name, setName] = useState("");
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const { caps, bind } = useCapsLock();

  const isRegister = mode === "register";

  function switchMode(next: Mode) {
    if (next === mode || pending) return;
    setMode(next);
    setError(null);
    setSuccess(null);
    setPassword("");
    setPasswordConfirm("");
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (pending) return;
    setError(null);
    setSuccess(null);

    const id = loginId.trim().toLowerCase();

    if (isRegister) {
      if (password !== passwordConfirm) {
        setError(MISMATCH_MESSAGE);
        return;
      }
      setPending(true);
      try {
        const message = await register(id, name.trim(), password);
        setSuccess(message);
        setMode("login");
        setName("");
        setPassword("");
        setPasswordConfirm("");
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setPending(false);
      }
      return;
    }

    setPending(true);
    try {
      await login(id, password);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-shell">
      <header className="app-header">
        <img className="auth-logo" src={`${import.meta.env.BASE_URL}logo-full.png`} alt="" width="220" height="152" />
        <h1 className="wordmark sr-only">MABLOP</h1>
        <p className="app-subtitle">쌓일수록 나다워지는 글쓰기 AI</p>
      </header>

      <div className="mode-tabs" role="tablist" aria-label="로그인 방식">
        <button
          type="button"
          role="tab"
          id="auth-tab-login"
          aria-selected={!isRegister}
          aria-controls="auth-panel"
          className={!isRegister ? "active" : undefined}
          onClick={() => switchMode("login")}
        >
          로그인
        </button>
        <button
          type="button"
          role="tab"
          id="auth-tab-register"
          aria-selected={isRegister}
          aria-controls="auth-panel"
          className={isRegister ? "active" : undefined}
          onClick={() => switchMode("register")}
        >
          가입 신청
        </button>
      </div>

      <div id="auth-panel" role="tabpanel" aria-labelledby={isRegister ? "auth-tab-register" : "auth-tab-login"}>
        <form onSubmit={onSubmit} noValidate>
          {isRegister && (
            <label className="field">
              이름
              <input
                type="text"
                name="name"
                autoComplete="name"
                value={name}
                maxLength={30}
                required
                onChange={(e) => setName(e.target.value)}
              />
            </label>
          )}

          <div className="field-group">
            <label className="field">
              아이디
              <input
                type="text"
                name="loginId"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={loginId}
                maxLength={32}
                required
                aria-describedby={isRegister ? "login-id-hint" : undefined}
                onChange={(e) => setLoginId(e.target.value)}
              />
            </label>
            {isRegister && (
              <p className="field-hint" id="login-id-hint">
                영문 소문자, 숫자, _, - 만 사용해 4~32자로 입력하세요.
              </p>
            )}
          </div>

          <div className="field-group">
            <label className="field">
              비밀번호
              <input
                type="password"
                name="password"
                {...bind}
                autoComplete={isRegister ? "new-password" : "current-password"}
                value={password}
                maxLength={128}
                required
                aria-describedby={isRegister ? "password-hint" : undefined}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {isRegister && (
              <p className="field-hint" id="password-hint">
                10~128자로 입력하세요.
              </p>
            )}
          </div>

          <CapsLockHint state={caps} />

          {isRegister && (
            <label className="field">
              비밀번호 확인
              <input
                type="password"
                name="passwordConfirm"
                {...bind}
                autoComplete="new-password"
                value={passwordConfirm}
                maxLength={128}
                required
                onChange={(e) => setPasswordConfirm(e.target.value)}
              />
            </label>
          )}

          {notice && !error && !success && (
            <div className="callout" role="status">
              <Info size={16} aria-hidden="true" />
              <p>{notice}</p>
            </div>
          )}
          {success && (
            <div className="callout" role="status">
              <Info size={16} aria-hidden="true" />
              <p>{success}</p>
            </div>
          )}
          {error && (
            <div className="callout callout-error" role="alert">
              <CircleAlert size={16} aria-hidden="true" />
              <p>{error}</p>
            </div>
          )}

          <button type="submit" className="btn-primary btn-block" disabled={pending}>
            {pending ? "처리 중..." : isRegister ? "가입 신청" : "로그인"}
          </button>
          {!isRegister && (
            <button type="button" className="btn-link-quiet" onClick={() => setHelpOpen(true)}>
              비밀번호를 잊으셨나요?
            </button>
          )}
        </form>
      </div>
      {helpOpen && <PasswordHelpDialog initialLoginId={loginId} onClose={() => setHelpOpen(false)} />}
      <SiteFooter />
    </main>
  );
}
