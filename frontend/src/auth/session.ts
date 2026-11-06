// Storage helpers for the login session (Req 13.5, 13.6) and the one-time
// purge of legacy localStorage entries (Req 10.3, 23.11).

export type SessionRole = "admin" | "member";

export interface SessionUser {
  loginId: string;
  name: string;
  role: SessionRole;
}

export interface Session {
  token: string;
  expiresAt: string;
  user: SessionUser;
}

export const SESSION_KEY = "mablop-session";

// Legacy keys. These two strings live only in this file and are used only for
// removal (Req 10.2, 23.10 read as "no component reads or writes them").
const LEGACY_TOKEN_KEY = "mablop-token";
const LEGACY_DRAFT_KEY_PREFIX = "mablop-last-draft";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSession(value: unknown): value is Session {
  if (!isRecord(value)) return false;
  const { token, expiresAt, user } = value;
  if (typeof token !== "string" || token === "") return false;
  if (typeof expiresAt !== "string" || expiresAt === "") return false;
  if (!isRecord(user)) return false;
  return (
    typeof user.loginId === "string" &&
    typeof user.name === "string" &&
    (user.role === "admin" || user.role === "member")
  );
}

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

// Returns the stored session, or null. A missing entry gives null. An entry with
// invalid JSON, a wrong shape, or an expired/unparseable expiresAt is removed
// first (Req 13.6). Expiry boundary: Date.parse(expiresAt) <= now is expired.
export function readSession(now: number = Date.now()): Session | null {
  const store = storage();
  if (!store) return null;
  let raw: string | null;
  try {
    raw = store.getItem(SESSION_KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    clearSession();
    return null;
  }
  if (!isSession(parsed)) {
    clearSession();
    return null;
  }
  const expiresMs = Date.parse(parsed.expiresAt);
  if (Number.isNaN(expiresMs) || expiresMs <= now) {
    clearSession();
    return null;
  }
  return {
    token: parsed.token,
    expiresAt: parsed.expiresAt,
    user: {
      loginId: parsed.user.loginId,
      name: parsed.user.name,
      role: parsed.user.role,
    },
  };
}

export function writeSession(session: Session): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(
      SESSION_KEY,
      JSON.stringify({
        token: session.token,
        expiresAt: session.expiresAt,
        user: {
          loginId: session.user.loginId,
          name: session.user.name,
          role: session.user.role,
        },
      }),
    );
  } catch {
    // Storage full or blocked: the caller keeps its in-memory state.
  }
}

export function clearSession(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(SESSION_KEY);
  } catch {
    // Ignore storage errors.
  }
}

// Removes the old shared-token entry and every entry whose key starts with the
// legacy draft-copy prefix. Other keys (mablop-session, mablop-grant-id:*) stay.
export function purgeLegacyStorage(): void {
  const store = storage();
  if (!store) return;
  try {
    // Collect first: removing while iterating shifts the indexes.
    const doomed: string[] = [];
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i);
      if (key !== null && key.startsWith(LEGACY_DRAFT_KEY_PREFIX)) {
        doomed.push(key);
      }
    }
    for (const key of doomed) {
      store.removeItem(key);
    }
    store.removeItem(LEGACY_TOKEN_KEY);
  } catch {
    // Ignore storage errors.
  }
}
