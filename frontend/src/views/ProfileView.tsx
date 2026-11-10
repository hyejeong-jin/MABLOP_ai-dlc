// User page "내 정보" (Req 14, 19). Account, learning summary, password change,
// logout, and, for members only, the "AI 서비스 토큰 사용 권한" section.
// Loads get-me and get-learning-status in parallel. A failure in one section
// never hides the others. Browser -> Lambda only; no form lib, no state manager.
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { CircleAlert, Info, KeyRound, LogOut, ShieldCheck, User } from "lucide-react";

import { ApiRequestError, postJson } from "../api";
import { useAuth } from "../auth/AuthContext";
import { formatKst, formatKstDate } from "../format";
import { UsageSection } from "../components/UsageSection";
import { CapsLockHint, useCapsLock } from "../components/CapsLock";
import { GRANT_REQUEST_HINT } from "../grants/GrantSelect";

interface MeResponse {
  userId?: string;
  loginId: string;
  name: string;
  role?: string;
  status?: string;
  createdAt?: string;
}

interface LearningStatus {
  updatedAt?: string;
  processedPosts: number;
  chunks: number;
}

interface ChangePasswordResponse {
  token: string;
  expiresAt: string;
}

interface Grant {
  id: string;
  status: string;
  purpose: string | null;
  requestedAt: string;
  decidedAt: string | null;
  expiresAt: string | null;
  expired: boolean;
}

interface GrantListResponse {
  grants: Grant[];
}

const MISMATCH_MESSAGE = "비밀번호가 일치하지 않습니다.";
const PASSWORD_CHANGED_MESSAGE = "비밀번호가 변경되었습니다.";
const NO_LEARNING_MESSAGE = "아직 학습한 글이 없습니다.";
const PENDING_GRANT_MESSAGE = "승인 대기 중인 신청이 있습니다.";
const NO_GRANTS_MESSAGE = "아직 신청한 사용 권한이 없습니다.";
const ME_ERROR_MESSAGE = "내 정보를 불러오지 못했습니다.";
const LEARNING_ERROR_MESSAGE = "학습 현황을 불러오지 못했습니다.";
const PASSWORD_ERROR_MESSAGE = "비밀번호를 변경하지 못했습니다. 잠시 후 다시 시도하세요.";
const GRANT_LIST_ERROR_MESSAGE = "AI 서비스 토큰 사용 권한 목록을 불러오지 못했습니다.";
const GRANT_REQUEST_ERROR_MESSAGE = "사용 권한을 신청하지 못했습니다. 잠시 후 다시 시도하세요.";
const PURPOSE_MAX_LENGTH = 100;

function messageOf(err: unknown, fallback: string): string {
  if (err instanceof ApiRequestError && err.message) return err.message;
  return fallback;
}

interface BadgeInfo {
  label: string;
  className: string;
}

function badgeFor(grant: Grant): BadgeInfo {
  if (grant.status === "approved") {
    return grant.expired
      ? { label: "만료", className: "badge badge-disabled" }
      : { label: "승인", className: "badge badge-active" };
  }
  if (grant.status === "requested") return { label: "대기", className: "badge badge-pending" };
  if (grant.status === "rejected") return { label: "거절", className: "badge badge-disabled" };
  if (grant.status === "revoked") return { label: "폐기", className: "badge badge-disabled" };
  return { label: grant.status, className: "badge" };
}

function Alert({ message }: { message: string }) {
  return (
    <div className="callout callout-error" role="alert">
      <CircleAlert size={16} aria-hidden="true" />
      <p>{message}</p>
    </div>
  );
}

function GrantSection() {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [purpose, setPurpose] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  // Each list load gets a number; only the latest one may write state.
  const loadRef = useRef(0);

  const loadGrants = useCallback(async (): Promise<boolean> => {
    loadRef.current += 1;
    const loadId = loadRef.current;
    try {
      const res = await postJson<GrantListResponse>({ action: "grant-list-mine" });
      if (!mountedRef.current || loadId !== loadRef.current) return true;
      setGrants(Array.isArray(res?.grants) ? res.grants : []);
      setLoaded(true);
      return true;
    } catch (err) {
      if (!mountedRef.current || loadId !== loadRef.current) return false;
      setError(messageOf(err, GRANT_LIST_ERROR_MESSAGE));
      setLoaded(true);
      return false;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void loadGrants();
    return () => {
      mountedRef.current = false;
    };
  }, [loadGrants]);

  const hasRequested = grants.some((g) => g.status === "requested");
  const formDisabled = hasRequested || submitting;

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (formDisabled) return;
    setError(null);
    setSubmitting(true);
    try {
      const trimmed = purpose.trim();
      await postJson({ action: "grant-request", purpose: trimmed === "" ? null : trimmed });
      if (!mountedRef.current) return;
      setPurpose("");
      await loadGrants();
    } catch (err) {
      if (mountedRef.current) setError(messageOf(err, GRANT_REQUEST_ERROR_MESSAGE));
    } finally {
      if (mountedRef.current) setSubmitting(false);
    }
  }

  return (
    <section className="card" aria-labelledby="profile-grants-title">
      <h2 className="section-title" id="profile-grants-title">
        AI 서비스 토큰 사용 권한
      </h2>
      <p className="field-hint">{GRANT_REQUEST_HINT}</p>

      <form onSubmit={onSubmit} className="stack" noValidate>
        <div className="field-group">
          <label className="field">
            사용 목적
            <input
              type="text"
              name="purpose"
              value={purpose}
              maxLength={PURPOSE_MAX_LENGTH}
              disabled={formDisabled}
              aria-describedby="profile-purpose-hint"
              onChange={(e) => setPurpose(e.target.value)}
            />
          </label>
          <p className="field-hint" id="profile-purpose-hint">
            선택 입력입니다. 100자까지 쓸 수 있습니다.
          </p>
        </div>

        {hasRequested && (
          <div className="callout">
            <Info size={16} aria-hidden="true" />
            <p>{PENDING_GRANT_MESSAGE}</p>
          </div>
        )}

        <div>
          <button
            type="submit"
            className="btn-primary btn-with-icon"
            disabled={formDisabled}
          >
            <ShieldCheck size={16} aria-hidden="true" />
            사용 권한 신청
          </button>
        </div>
      </form>

      {error && (
        <div style={{ marginTop: 16 }}>
          <Alert message={error} />
        </div>
      )}

      <div style={{ marginTop: 20 }}>
        {loaded && grants.length === 0 && !error && <p className="meta">{NO_GRANTS_MESSAGE}</p>}
        {grants.length > 0 && (
          <ul className="plain-list" aria-label="사용 권한 신청 내역">
            {grants.map((grant) => {
              const badge = badgeFor(grant);
              return (
                <li key={grant.id} className="grant-row">
                  <div>
                    <span className={badge.className}>{badge.label}</span>
                    {grant.purpose && <span style={{ marginLeft: 8 }}>{grant.purpose}</span>}
                    <div className="sub">신청 {formatKst(grant.requestedAt)}</div>
                  </div>
                  {grant.status === "approved" && grant.expiresAt && (
                    <span className="sub mono">만료일 {formatKstDate(grant.expiresAt)}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

export function ProfileView() {
  const { user, logout, replaceToken } = useAuth();
  const { caps, bind } = useCapsLock();

  const [me, setMe] = useState<MeResponse | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  const [learning, setLearning] = useState<LearningStatus | null>(null);
  const [learningError, setLearningError] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");
  const [changing, setChanging] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);

  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    postJson<MeResponse>({ action: "get-me" })
      .then((res) => {
        if (mountedRef.current) setMe(res);
      })
      .catch((err: unknown) => {
        if (mountedRef.current) setMeError(messageOf(err, ME_ERROR_MESSAGE));
      });
    postJson<LearningStatus>({ action: "get-learning-status" })
      .then((res) => {
        if (mountedRef.current) setLearning(res);
      })
      .catch((err: unknown) => {
        if (mountedRef.current) setLearningError(messageOf(err, LEARNING_ERROR_MESSAGE));
      });
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function onChangePassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (changing) return;
    setPasswordError(null);
    setPasswordSuccess(null);
    if (newPassword !== newPasswordConfirm) {
      setPasswordError(MISMATCH_MESSAGE);
      return;
    }
    setChanging(true);
    try {
      const res = await postJson<ChangePasswordResponse>({
        action: "change-password",
        currentPassword,
        newPassword,
      });
      replaceToken(res.token, res.expiresAt);
      if (!mountedRef.current) return;
      setCurrentPassword("");
      setNewPassword("");
      setNewPasswordConfirm("");
      setPasswordSuccess(PASSWORD_CHANGED_MESSAGE);
    } catch (err) {
      if (mountedRef.current) setPasswordError(messageOf(err, PASSWORD_ERROR_MESSAGE));
    } finally {
      if (mountedRef.current) setChanging(false);
    }
  }

  return (
    <div className="stack">
      <header>
        <h2 className="page-title">마이페이지</h2>
      </header>

      <section className="card" aria-labelledby="profile-account-title">
        <h3 className="section-title btn-with-icon" id="profile-account-title">
          <User size={16} aria-hidden="true" />
          계정
        </h3>
        {meError && <Alert message={meError} />}
        {!meError && me && (
          <dl className="info-list">
            <dt>이름</dt>
            <dd>{me.name}</dd>
            <dt>아이디</dt>
            <dd>{me.loginId}</dd>
          </dl>
        )}
      </section>

      <section className="card" aria-labelledby="profile-learning-title">
        <h3 className="section-title" id="profile-learning-title">
          학습 요약
        </h3>
        {learningError && <Alert message={learningError} />}
        {!learningError && learning && learning.processedPosts === 0 && (
          <p className="meta">{NO_LEARNING_MESSAGE}</p>
        )}
        {!learningError && learning && learning.processedPosts > 0 && (
          <div className="result-meta">
            <p>학습한 글 {learning.processedPosts}개</p>
            <p>청크 {learning.chunks}개</p>
            {learning.updatedAt && <p>최근 학습 {formatKst(learning.updatedAt)}</p>}
          </div>
        )}
      </section>

      <section className="card" aria-labelledby="profile-password-title">
        <h3 className="section-title btn-with-icon" id="profile-password-title">
          <KeyRound size={16} aria-hidden="true" />
          비밀번호
        </h3>
        <form onSubmit={onChangePassword} className="stack" noValidate>
          <label className="field">
            현재 비밀번호
            <input
              type="password"
              name="currentPassword"
              {...bind}
              autoComplete="current-password"
              value={currentPassword}
              maxLength={128}
              required
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </label>
          <label className="field">
            새 비밀번호
            <input
              type="password"
              name="newPassword"
              {...bind}
              autoComplete="new-password"
              value={newPassword}
              maxLength={128}
              required
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </label>
          <label className="field">
            새 비밀번호 확인
            <input
              type="password"
              name="newPasswordConfirm"
              {...bind}
              autoComplete="new-password"
              value={newPasswordConfirm}
              maxLength={128}
              required
              onChange={(e) => setNewPasswordConfirm(e.target.value)}
            />
          </label>

          <CapsLockHint state={caps} />

          {passwordSuccess && (
            <div className="callout" role="status">
              <Info size={16} aria-hidden="true" />
              <p>{passwordSuccess}</p>
            </div>
          )}
          {passwordError && <Alert message={passwordError} />}

          <div>
            <button type="submit" className="btn-primary" disabled={changing}>
              {changing ? "처리 중..." : "비밀번호 변경"}
            </button>
          </div>
        </form>
      </section>

      <UsageSection />
        {user?.role === "member" && <GrantSection />}

      <div>
        <button type="button" className="btn-with-icon" onClick={logout}>
          <LogOut size={16} aria-hidden="true" />
          로그아웃
        </button>
      </div>
    </div>
  );
}
