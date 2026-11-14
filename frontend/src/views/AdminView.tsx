// Administrator page "관리" (Req 15.1-15.18, 9.18, 9.19, 19).
// Three sections: pending registrations, all users, and "권한 관리" for Bedrock usage Grants.
// The user list and the Grant list load independently, so one failing does not blank the other.
// A temporary password and the legacy-binding notices live in component state only (Req 15.7).
import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, Copy } from "lucide-react";

import { ApiRequestError, postJson, VOC_STATUS_LABELS, type VocStatus } from "../api";
import { formatKst, formatKstDate } from "../format";

type UserStatus = "pending" | "active" | "disabled";
type UserAction = "approve" | "reject" | "disable" | "enable" | "reset" | "delete";
type GrantAction = "approve" | "reject" | "revoke";
type ExpiryDays = 7 | 30 | 90;

interface AdminUser {
  userId: string;
  loginId: string;
  name: string;
  role: "admin" | "member";
  status: UserStatus;
  createdAt: string;
  approvedAt: string | null;
}

interface AdminGrant {
  userId: string;
  loginId: string;
  name: string;
  id: string;
  status: "requested" | "approved" | "rejected" | "revoked";
  purpose: string | null;
  requestedAt: string;
  decidedAt: string | null;
  expiresAt: string | null;
  expired: boolean;
}

interface LegacyBinding {
  status: "not_applicable" | "succeeded" | "failed" | "already_bound";
  message: string | null;
}

interface ApproveResponse {
  userId: string;
  status: string;
  legacyBinding?: LegacyBinding;
}

interface ResetResponse {
  userId: string;
  temporaryPassword: string;
}

interface TempPassword {
  name: string;
  loginId: string;
  value: string;
}

const FALLBACK_MESSAGE = "요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.";
const LEGACY_FALLBACK_MESSAGE = "기존 데이터 연결에 실패했습니다. 승인은 유지되었습니다.";
const COPY_FAILED_MESSAGE = "복사하지 못했습니다. 비밀번호를 직접 선택해 복사하세요.";
const DEFAULT_EXPIRY_DAYS: ExpiryDays = 30;
const EXPIRY_CHOICES: readonly ExpiryDays[] = [7, 30, 90];

const USER_ACTION_NAME: Record<UserAction, string> = {
  approve: "admin-approve-user",
  reject: "admin-reject-user",
  disable: "admin-disable-user",
  enable: "admin-enable-user",
  reset: "admin-reset-password",
  delete: "admin-delete-user",
};

const GRANT_ACTION_NAME: Record<GrantAction, string> = {
  approve: "admin-grant-approve",
  reject: "admin-grant-reject",
  revoke: "admin-grant-revoke",
};

const USER_CONFIRM_TEXT: Partial<Record<UserAction, string>> = {
  reject: "이 가입 신청을 거절할까요?",
  disable: "이 계정을 비활성화할까요?",
  reset: "비밀번호를 초기화할까요?",
  delete: "정말 삭제할까요? 이 사용자의 모든 데이터가 영구 삭제됩니다.",
};

const GRANT_CONFIRM_TEXT: Partial<Record<GrantAction, string>> = {
  reject: "이 사용 권한 신청을 거절할까요?",
  revoke: "이 사용 권한을 폐기할까요?",
};

const STATUS_LABEL: Record<UserStatus, string> = {
  pending: "대기",
  active: "활성",
  disabled: "비활성",
};

const STATUS_BADGE_CLASS: Record<UserStatus, string> = {
  pending: "badge badge-pending",
  active: "badge badge-active",
  disabled: "badge badge-disabled",
};

function errorMessage(err: unknown): string {
  if (err instanceof ApiRequestError && err.message) return err.message;
  return FALLBACK_MESSAGE;
}

function grantKey(userId: string, grantId: string): string {
  return `${userId}:${grantId}`;
}

function withKey<T>(set: ReadonlySet<T>, key: T): Set<T> {
  const next = new Set(set);
  next.add(key);
  return next;
}

function withoutKey<T>(set: ReadonlySet<T>, key: T): Set<T> {
  const next = new Set(set);
  next.delete(key);
  return next;
}

function omitKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record;
  const next = { ...record };
  delete next[key];
  return next;
}

function grantBadge(grant: AdminGrant): { label: string; className: string } {
  if (grant.status === "approved" && grant.expired) {
    return { label: "만료", className: "badge badge-disabled" };
  }
  switch (grant.status) {
    case "approved":
      return { label: "승인", className: "badge badge-active" };
    case "rejected":
      return { label: "거절", className: "badge badge-disabled" };
    case "revoked":
      return { label: "폐기", className: "badge badge-disabled" };
    default:
      return { label: "대기", className: "badge badge-pending" };
  }
}

function SectionError({ message }: { message: string }) {
  return (
    <div className="callout callout-error" role="alert">
      <CircleAlert size={16} aria-hidden="true" />
      <p>{message}</p>
    </div>
  );
}

interface AdminVocRecord {
  vocId: string;
  ownerId: string;
  preview?: string;
  status: VocStatus;
  createdAt?: string;
}

const VOC_STATUSES = Object.keys(VOC_STATUS_LABELS) as VocStatus[];

function VoiceBoxAdminSection() {
  const [filter, setFilter] = useState<"" | VocStatus>("");
  const [records, setRecords] = useState<AdminVocRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});
  const request = useRef(0);

  const load = useCallback(async (status: "" | VocStatus) => {
    const id = ++request.current;
    setLoading(true);
    try {
      const body: Record<string, unknown> = { action: "admin-list-voc" };
      if (status) body.status = status;
      const res = await postJson<{ records: AdminVocRecord[] }>(body);
      if (id !== request.current) return;
      setRecords(res.records ?? []);
      setError(null);
    } catch (err) {
      if (id !== request.current) return;
      setError(errorMessage(err));
    } finally {
      if (id === request.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filter);
  }, [load, filter]);

  async function changeStatus(record: AdminVocRecord, status: VocStatus) {
    if (busyId) return;
    setBusyId(record.vocId);
    setRowError((prev) => omitKey(prev, record.vocId));
    try {
      await postJson({
        action: "admin-update-voc-status",
        userId: record.ownerId,
        vocId: record.vocId,
        status,
      });
      await load(filter);
    } catch (err) {
      setRowError((prev) => ({ ...prev, [record.vocId]: errorMessage(err) }));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="card" aria-labelledby="admin-voc-title">
      <h3 className="section-title" id="admin-voc-title">
        목소리함 관리
      </h3>
      <label className="field">
        상태 필터
        <select
          aria-label="목소리함 상태 필터"
          value={filter}
          onChange={(e) => setFilter(e.target.value as "" | VocStatus)}
        >
          <option value="">전체</option>
          {VOC_STATUSES.map((s) => (
            <option key={s} value={s}>
              {VOC_STATUS_LABELS[s]}
            </option>
          ))}
        </select>
      </label>
      {error && <SectionError message={error} />}
      {loading && !error && (
        <p className="meta" role="status">
          불러오는 중...
        </p>
      )}
      {!loading && !error && records.length === 0 && <p className="meta">표시할 의견이 없습니다.</p>}
      {records.map((record) => (
        <div className="user-row" key={`${record.ownerId}:${record.vocId}`}>
          <div>
            <strong>{record.preview || "(내용 없음)"}</strong>
            <div className="sub">
              작성자 {record.ownerId}
              {record.createdAt ? ` · ${formatKst(record.createdAt)}` : ""}
            </div>
          </div>
          <select
            aria-label={`의견 상태 변경 ${record.vocId}`}
            value={record.status}
            disabled={busyId !== null}
            onChange={(e) => void changeStatus(record, e.target.value as VocStatus)}
          >
            {VOC_STATUSES.map((s) => (
              <option key={s} value={s}>
                {VOC_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
          {rowError[record.vocId] && <SectionError message={rowError[record.vocId]} />}
        </div>
      ))}
    </section>
  );
}

export function AdminView() {  // Users
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ userId: string; action: UserAction } | null>(null);
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [legacyNotices, setLegacyNotices] = useState<Record<string, string>>({});
  const [tempPassword, setTempPassword] = useState<TempPassword | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  // Grants
  const [grants, setGrants] = useState<AdminGrant[]>([]);
  const [grantsLoading, setGrantsLoading] = useState(true);
  const [grantsError, setGrantsError] = useState<string | null>(null);
  const [confirmingGrant, setConfirmingGrant] = useState<{
    userId: string;
    grantId: string;
    action: GrantAction;
  } | null>(null);
  const [grantBusy, setGrantBusy] = useState<ReadonlySet<string>>(new Set());
  const [grantErrors, setGrantErrors] = useState<Record<string, string>>({});
  const [expiryDays, setExpiryDays] = useState<Record<string, ExpiryDays>>({});

  // A response that arrives after a newer request started is discarded.
  const usersRequest = useRef(0);
  const grantsRequest = useRef(0);

  const loadUsers = useCallback(async () => {
    const request = ++usersRequest.current;
    try {
      const res = await postJson<{ users: AdminUser[] }>({ action: "admin-list-users" });
      if (request !== usersRequest.current) return;
      setUsers(res.users ?? []);
      setUsersError(null);
    } catch (err) {
      if (request !== usersRequest.current) return;
      setUsersError(errorMessage(err));
    } finally {
      if (request === usersRequest.current) setUsersLoading(false);
    }
  }, []);

  const loadGrants = useCallback(async () => {
    const request = ++grantsRequest.current;
    try {
      const res = await postJson<{ grants: AdminGrant[] }>({ action: "admin-grant-list" });
      if (request !== grantsRequest.current) return;
      setGrants(res.grants ?? []);
      setGrantsError(null);
    } catch (err) {
      if (request !== grantsRequest.current) return;
      setGrantsError(errorMessage(err));
    } finally {
      if (request === grantsRequest.current) setGrantsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUsers();
    void loadGrants();
  }, [loadUsers, loadGrants]);

  function openUserConfirm(userId: string, action: UserAction) {
    setConfirmingGrant(null);
    setConfirming({ userId, action });
  }

  function openGrantConfirm(userId: string, grantId: string, action: GrantAction) {
    setConfirming(null);
    setConfirmingGrant({ userId, grantId, action });
  }

  async function runUserAction(user: AdminUser, action: UserAction) {
    const { userId } = user;
    if (busy.has(userId)) return;
    setConfirming(null);
    setBusy((prev) => withKey(prev, userId));
    setRowErrors((prev) => omitKey(prev, userId));
    try {
      const res = await postJson<ApproveResponse | ResetResponse | Record<string, unknown>>({
        action: USER_ACTION_NAME[action],
        userId,
      });
      if (action === "approve") {
        const binding = (res as ApproveResponse).legacyBinding;
        if (binding && (binding.status === "failed" || binding.status === "already_bound")) {
          setLegacyNotices((prev) => ({
            ...prev,
            [userId]: binding.message || LEGACY_FALLBACK_MESSAGE,
          }));
        } else {
          setLegacyNotices((prev) => omitKey(prev, userId));
        }
      }
      if (action === "reset") {
        setTempPassword({
          name: user.name,
          loginId: user.loginId,
          value: (res as ResetResponse).temporaryPassword,
        });
        setCopyState("idle");
      }
      if (action === "reject" || action === "delete") {
        setLegacyNotices((prev) => omitKey(prev, userId));
      }
      await loadUsers();
    } catch (err) {
      setRowErrors((prev) => ({ ...prev, [userId]: errorMessage(err) }));
    } finally {
      setBusy((prev) => withoutKey(prev, userId));
    }
  }

  async function runGrantAction(grant: AdminGrant, action: GrantAction) {
    const key = grantKey(grant.userId, grant.id);
    if (grantBusy.has(key)) return;
    setConfirmingGrant(null);
    setGrantBusy((prev) => withKey(prev, key));
    setGrantErrors((prev) => omitKey(prev, key));
    try {
      const body: Record<string, unknown> = {
        action: GRANT_ACTION_NAME[action],
        userId: grant.userId,
        grantId: grant.id,
      };
      if (action === "approve") {
        body.expiresInDays = expiryDays[key] ?? DEFAULT_EXPIRY_DAYS;
      }
      await postJson(body);
      await loadGrants();
    } catch (err) {
      setGrantErrors((prev) => ({ ...prev, [key]: errorMessage(err) }));
    } finally {
      setGrantBusy((prev) => withoutKey(prev, key));
    }
  }

  async function copyTempPassword() {
    if (!tempPassword) return;
    try {
      await navigator.clipboard.writeText(tempPassword.value);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  function dismissTempPassword() {
    setTempPassword(null);
    setCopyState("idle");
  }

  function renderUserAlerts(user: AdminUser) {
    const rowError = rowErrors[user.userId];
    const legacy = legacyNotices[user.userId];
    return (
      <>
        {rowError && <SectionError message={rowError} />}
        {legacy && <SectionError message={legacy} />}
      </>
    );
  }

  function renderConfirm(text: string, onConfirm: () => void, onCancel: () => void) {
    return (
      <span className="confirm-inline">
        <span>{text}</span>
        <button type="button" className="btn-sm btn-primary" onClick={onConfirm}>
          확인
        </button>
        <button type="button" className="btn-sm" onClick={onCancel}>
          취소
        </button>
      </span>
    );
  }

  const pendingUsers = users.filter((u) => u.status === "pending");
  const requestedGrants = grants.filter((g) => g.status === "requested");
  const issuedGrants = grants.filter((g) => g.status !== "requested");

  return (
    <section>
      <h2 className="page-title">관리</h2>
      <p className="page-lead">가입 신청과 사용자, AI 서비스 토큰 사용 권한을 한곳에서 관리합니다.</p>

      <div className="stack">
        {/* 가입 신청 */}
        <section className="card" aria-labelledby="admin-pending-title">
          <h3 className="section-title" id="admin-pending-title">
            가입 신청
          </h3>
          {usersError && <SectionError message={usersError} />}
          {usersLoading && !usersError && (
            <p className="meta" role="status">
              불러오는 중...
            </p>
          )}
          {!usersLoading && !usersError && pendingUsers.length === 0 && (
            <p className="meta">대기 중인 가입 신청이 없습니다.</p>
          )}
          {pendingUsers.length > 0 && (
            <div>
              {pendingUsers.map((user) => {
                const isBusy = busy.has(user.userId);
                const isConfirmingReject =
                  confirming?.userId === user.userId && confirming.action === "reject";
                return (
                  <div className="user-row" key={user.userId}>
                    <div>
                      <strong>{user.name}</strong>
                      <div className="sub">
                        아이디 {user.loginId} · 신청 {formatKst(user.createdAt)}
                      </div>
                    </div>
                    <div className="user-row-actions">
                      {isConfirmingReject ? (
                        renderConfirm(
                          USER_CONFIRM_TEXT.reject ?? "",
                          () => void runUserAction(user, "reject"),
                          () => setConfirming(null),
                        )
                      ) : (
                        <>
                          <button
                            type="button"
                            className="btn-sm btn-primary"
                            disabled={isBusy}
                            onClick={() => void runUserAction(user, "approve")}
                          >
                            승인
                          </button>
                          <button
                            type="button"
                            className="btn-sm"
                            disabled={isBusy}
                            onClick={() => openUserConfirm(user.userId, "reject")}
                          >
                            거절
                          </button>
                        </>
                      )}
                    </div>
                    {renderUserAlerts(user)}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* 전체 사용자 */}
        <section className="card" aria-labelledby="admin-users-title">
          <h3 className="section-title" id="admin-users-title">
            전체 사용자
          </h3>

          {tempPassword && (
            <div className="secret-box" role="group" aria-label="임시 비밀번호">
              <p>
                {tempPassword.name}({tempPassword.loginId})의 임시 비밀번호입니다.
              </p>
              <code>{tempPassword.value}</code>
              <p>이 비밀번호는 다시 표시되지 않습니다.</p>
              <div className="user-row-actions">
                <button type="button" className="btn-sm btn-with-icon" onClick={() => void copyTempPassword()}>
                  <Copy size={14} aria-hidden="true" />
                  복사
                </button>
                <button type="button" className="btn-sm" onClick={dismissTempPassword}>
                  닫기
                </button>
                {copyState === "copied" && (
                  <span role="status" className="meta">
                    복사됨
                  </span>
                )}
                {copyState === "failed" && (
                  <span role="alert" className="meta">
                    {COPY_FAILED_MESSAGE}
                  </span>
                )}
              </div>
            </div>
          )}

          {usersLoading && !usersError && (
            <p className="meta" role="status">
              불러오는 중...
            </p>
          )}
          {!usersLoading && !usersError && users.length === 0 && (
            <p className="meta">등록된 사용자가 없습니다.</p>
          )}
          {users.length > 0 && (
            <div>
              {users.map((user) => {
                const isBusy = busy.has(user.userId);
                const isMember = user.role === "member";
                const activeConfirm =
                  confirming?.userId === user.userId && confirming.action !== "reject"
                    ? confirming.action
                    : null;
                return (
                  <div className="user-row" key={user.userId}>
                    <div>
                      <strong>{user.name}</strong>
                      <div className="sub">
                        아이디 {user.loginId}
                        {user.role === "admin" ? " · 관리자" : ""} · 가입일 {formatKstDate(user.createdAt)}
                      </div>
                    </div>
                    <span className={STATUS_BADGE_CLASS[user.status]}>{STATUS_LABEL[user.status]}</span>
                    {isMember && (
                      <div className="user-row-actions">
                        {activeConfirm ? (
                          renderConfirm(
                            USER_CONFIRM_TEXT[activeConfirm] ?? "",
                            () => void runUserAction(user, activeConfirm),
                            () => setConfirming(null),
                          )
                        ) : (
                          <>
                            {user.status === "active" && (
                              <button
                                type="button"
                                className="btn-sm"
                                disabled={isBusy}
                                onClick={() => openUserConfirm(user.userId, "disable")}
                              >
                                비활성화
                              </button>
                            )}
                            {user.status === "disabled" && (
                              <button
                                type="button"
                                className="btn-sm"
                                disabled={isBusy}
                                onClick={() => void runUserAction(user, "enable")}
                              >
                                재활성화
                              </button>
                            )}
                            <button
                              type="button"
                              className="btn-sm"
                              disabled={isBusy}
                              onClick={() => openUserConfirm(user.userId, "reset")}
                            >
                              비밀번호 초기화
                            </button>
                            <button
                              type="button"
                              className="btn-sm btn-danger"
                              disabled={isBusy}
                              onClick={() => openUserConfirm(user.userId, "delete")}
                            >
                              계정 삭제
                            </button>
                          </>
                        )}
                      </div>
                    )}
                    {/* Pending users show their alerts in the "가입 신청" row instead. */}
                    {user.status !== "pending" && renderUserAlerts(user)}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* 권한 관리 */}
        <section className="card" aria-labelledby="admin-grants-title">
          <h3 className="section-title" id="admin-grants-title">
            권한 관리
          </h3>
          {grantsError && <SectionError message={grantsError} />}
          {grantsLoading && !grantsError && (
            <p className="meta" role="status">
              불러오는 중...
            </p>
          )}

          {!grantsLoading && (
            <div className="stack">
              <div>
                <h4 className="section-title">신청 대기</h4>
                {requestedGrants.length === 0 && !grantsError && (
                  <p className="meta">대기 중인 사용 권한 신청이 없습니다.</p>
                )}
                {requestedGrants.map((grant) => {
                  const key = grantKey(grant.userId, grant.id);
                  const isBusy = grantBusy.has(key);
                  const isConfirmingReject =
                    confirmingGrant?.userId === grant.userId &&
                    confirmingGrant.grantId === grant.id &&
                    confirmingGrant.action === "reject";
                  return (
                    <div className="grant-row" key={key}>
                      <div>
                        <strong>{grant.name}</strong>
                        <div className="sub">
                          아이디 {grant.loginId} · 사용 목적 {grant.purpose ?? "-"} · 신청{" "}
                          {formatKst(grant.requestedAt)}
                        </div>
                      </div>
                      <div className="user-row-actions">
                        {isConfirmingReject ? (
                          renderConfirm(
                            GRANT_CONFIRM_TEXT.reject ?? "",
                            () => void runGrantAction(grant, "reject"),
                            () => setConfirmingGrant(null),
                          )
                        ) : (
                          <>
                            <select
                              aria-label="만료 기간"
                              value={expiryDays[key] ?? DEFAULT_EXPIRY_DAYS}
                              disabled={isBusy}
                              onChange={(e) => {
                                const days = Number(e.target.value) as ExpiryDays;
                                setExpiryDays((prev) => ({ ...prev, [key]: days }));
                              }}
                            >
                              {EXPIRY_CHOICES.map((days) => (
                                <option key={days} value={days}>
                                  {days}일
                                </option>
                              ))}
                            </select>
                            <button
                              type="button"
                              className="btn-sm btn-primary"
                              disabled={isBusy}
                              onClick={() => void runGrantAction(grant, "approve")}
                            >
                              승인
                            </button>
                            <button
                              type="button"
                              className="btn-sm"
                              disabled={isBusy}
                              onClick={() => openGrantConfirm(grant.userId, grant.id, "reject")}
                            >
                              거절
                            </button>
                          </>
                        )}
                      </div>
                      {grantErrors[key] && <SectionError message={grantErrors[key]} />}
                    </div>
                  );
                })}
              </div>

              <div>
                <h4 className="section-title">발급 내역</h4>
                {issuedGrants.length === 0 && !grantsError && (
                  <p className="meta">발급된 사용 권한이 없습니다.</p>
                )}
                {issuedGrants.map((grant) => {
                  const key = grantKey(grant.userId, grant.id);
                  const isBusy = grantBusy.has(key);
                  const badge = grantBadge(grant);
                  const canRevoke = grant.status === "approved" && !grant.expired;
                  const isConfirmingRevoke =
                    confirmingGrant?.userId === grant.userId &&
                    confirmingGrant.grantId === grant.id &&
                    confirmingGrant.action === "revoke";
                  return (
                    <div className="grant-row" key={key}>
                      <div>
                        <strong>{grant.name}</strong>
                        <div className="sub">
                          아이디 {grant.loginId} · 만료일{" "}
                          {grant.expiresAt ? formatKstDate(grant.expiresAt) : "-"}
                        </div>
                      </div>
                      <span className={badge.className}>{badge.label}</span>
                      {canRevoke && (
                        <div className="user-row-actions">
                          {isConfirmingRevoke ? (
                            renderConfirm(
                              GRANT_CONFIRM_TEXT.revoke ?? "",
                              () => void runGrantAction(grant, "revoke"),
                              () => setConfirmingGrant(null),
                            )
                          ) : (
                            <button
                              type="button"
                              className="btn-sm"
                              disabled={isBusy}
                              onClick={() => openGrantConfirm(grant.userId, grant.id, "revoke")}
                            >
                              폐기
                            </button>
                          )}
                        </div>
                      )}
                      {grantErrors[key] && <SectionError message={grantErrors[key]} />}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </section>
        <VoiceBoxAdminSection />
      </div>
    </section>
  );
}
