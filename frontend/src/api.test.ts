import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  NETWORK_ERROR_MESSAGE,
  PUBLIC_ACTIONS,
  SESSION_EXPIRED_MESSAGE,
  postJson,
  setUnauthorizedHandler,
} from "./api";
import { SESSION_KEY, readSession, writeSession, type Session } from "./auth/session";

const TOKEN = "stored-session-token";
const HEADER = "X-Mablop-Token";

function storeSession(): void {
  const session: Session = {
    token: TOKEN,
    expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    user: { loginId: "alice", name: "앨리스", role: "member" },
  };
  writeSession(session);
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type FetchMock = ReturnType<typeof vi.fn>;

function lastHeaders(fetchMock: FetchMock): Record<string, string> {
  const init = fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1] as RequestInit;
  return init.headers as Record<string, string>;
}

let fetchMock: FetchMock;

beforeEach(() => {
  localStorage.clear();
  setUnauthorizedHandler(null);
  fetchMock = vi.fn(async () => jsonResponse(200, { ok: true }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
});

// Mulberry32 — tiny seeded PRNG (fast-check is not a dependency).
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Feature: user-auth-admin, Property 28 — Validates: Requirements 13.9
describe("Property 28: token header per action", () => {
  const publicActions = ["login", "register", "bootstrap-admin"];
  const authenticatedActions = [
    "get-me",
    "change-password",
    "migrate-legacy-data",
    "admin-list-users",
    "admin-approve-user",
    "admin-reject-user",
    "admin-disable-user",
    "admin-enable-user",
    "admin-reset-password",
    "grant-request",
    "grant-list-mine",
    "admin-grant-list",
    "learn-start",
    "get-learning-status",
    "generate-draft",
    "get-result",
    "list-drafts",
    "delete-draft",
    "preview-learning-sources",
    // near misses of the public names must still carry the token
    "Login",
    "LOGIN",
    "login ",
    " register",
    "bootstrap_admin",
    "bootstrap-admin2",
    "",
  ];

  it("PUBLIC_ACTIONS is exactly the three public actions", () => {
    expect([...PUBLIC_ACTIONS].sort()).toEqual([...publicActions].sort());
  });

  it.each(publicActions)("omits the header for public action %s", async (action) => {
    storeSession();
    await postJson({ action });
    expect(HEADER in lastHeaders(fetchMock)).toBe(false);
  });

  it.each(authenticatedActions)("attaches the stored token for action %j", async (action) => {
    storeSession();
    await postJson({ action });
    expect(lastHeaders(fetchMock)[HEADER]).toBe(TOKEN);
  });

  it("holds for random action names: header present iff not public", async () => {
    storeSession();
    const rand = mulberry32(0x28a28a28);
    const alphabet = "abcdefghijklmnopqrstuvwxyz-_ ";
    const names: string[] = [];
    for (let i = 0; i < 100; i++) {
      const len = 1 + Math.floor(rand() * 20);
      let s = "";
      for (let j = 0; j < len; j++) s += alphabet[Math.floor(rand() * alphabet.length)];
      names.push(s);
    }
    names.push(...publicActions);

    for (const action of names) {
      await postJson({ action });
      const has = lastHeaders(fetchMock)[HEADER] === TOKEN;
      expect(has).toBe(!publicActions.includes(action));
    }
  });

  it("omits the header for an authenticated action when no session is stored", async () => {
    await postJson({ action: "get-me" });
    expect(HEADER in lastHeaders(fetchMock)).toBe(false);
  });

  it("attaches the token when the body has no string action", async () => {
    storeSession();
    await postJson({ action: 42 });
    expect(lastHeaders(fetchMock)[HEADER]).toBe(TOKEN);
    await postJson({});
    expect(lastHeaders(fetchMock)[HEADER]).toBe(TOKEN);
  });

  it("ignores the legacy token argument", async () => {
    storeSession();
    await postJson({ action: "get-me" }, "legacy-shared-token");
    expect(lastHeaders(fetchMock)[HEADER]).toBe(TOKEN);
    await postJson({ action: "login" }, "legacy-shared-token");
    expect(HEADER in lastHeaders(fetchMock)).toBe(false);
  });

  it("sends a JSON POST with the body", async () => {
    storeSession();
    await postJson({ action: "get-me", x: 1 });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(lastHeaders(fetchMock)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ action: "get-me", x: 1 });
  });

  it("returns the parsed JSON body on success", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { hello: "world" }));
    await expect(postJson({ action: "login" })).resolves.toEqual({ hello: "world" });
  });
});

describe("401 handling", () => {
  it("clears the session, calls the handler, and throws the expired message for an authenticated action", async () => {
    storeSession();
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, { error: { code: "unauthorized", message: "server text" } }),
    );

    const err = (await postJson({ action: "get-me" }).catch((e) => e)) as ApiRequestError;

    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err.status).toBe(401);
    expect(err.code).toBe("unauthorized");
    expect(err.message).toBe(SESSION_EXPIRED_MESSAGE);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    expect(readSession()).toBeNull();
  });

  it("still throws 401 when the handler throws", async () => {
    storeSession();
    setUnauthorizedHandler(() => {
      throw new Error("boom");
    });
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    const err = (await postJson({ action: "get-me" }).catch((e) => e)) as ApiRequestError;
    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err.status).toBe(401);
    expect(err.code).toBe("unauthorized");
    expect(err.message).toBe(SESSION_EXPIRED_MESSAGE);
  });

  it("does not call the handler or clear the session for a 401 on login", async () => {
    storeSession();
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, {
        error: { code: "unauthorized", message: "아이디 또는 비밀번호가 올바르지 않습니다." },
      }),
    );

    const err = (await postJson({ action: "login" }).catch((e) => e)) as ApiRequestError;

    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err.status).toBe(401);
    expect(err.message).toBe("아이디 또는 비밀번호가 올바르지 않습니다.");
    expect(handler).not.toHaveBeenCalled();
    expect(readSession()).not.toBeNull();
  });

  it.each(["register", "bootstrap-admin"])(
    "does not call the handler for a 401 on %s",
    async (action) => {
      const handler = vi.fn();
      setUnauthorizedHandler(handler);
      fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
      await expect(postJson({ action })).rejects.toBeInstanceOf(ApiRequestError);
      expect(handler).not.toHaveBeenCalled();
    },
  );

  it("does not call the handler for non-401 errors on authenticated actions", async () => {
    storeSession();
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockResolvedValueOnce(
      jsonResponse(403, { error: { code: "forbidden", message: "관리자 권한이 필요합니다." } }),
    );
    const err = (await postJson({ action: "admin-list-users" }).catch((e) => e)) as ApiRequestError;
    expect(err.status).toBe(403);
    expect(err.code).toBe("forbidden");
    expect(err.message).toBe("관리자 권한이 필요합니다.");
    expect(handler).not.toHaveBeenCalled();
    expect(readSession()).not.toBeNull();
  });

  it("stops calling a handler that was unregistered with null", async () => {
    storeSession();
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    setUnauthorizedHandler(null);
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    await expect(postJson({ action: "get-me" })).rejects.toBeInstanceOf(ApiRequestError);
    expect(handler).not.toHaveBeenCalled();
  });
});

describe("error messages", () => {
  it("maps a network failure to the Korean network message", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const err = (await postJson({ action: "get-me" }).catch((e) => e)) as ApiRequestError;
    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err.status).toBe(0);
    expect(err.code).toBe("network");
    expect(err.message).toBe("서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.");
    expect(err.message).toBe(NETWORK_ERROR_MESSAGE);
  });

  it("uses a Korean fallback message when an error response is not JSON", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("<html>Bad Gateway</html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );
    const err = (await postJson({ action: "get-me" }).catch((e) => e)) as ApiRequestError;
    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err.status).toBe(502);
    expect(err.code).toBe("unknown");
    expect(err.message).toBe("요청을 처리하지 못했습니다. (HTTP 502)");
    expect(err.message).not.toContain("Bad Gateway");
  });

  it("uses the Korean expired message for a non-JSON 401 on an authenticated action", async () => {
    storeSession();
    fetchMock.mockResolvedValueOnce(new Response("nope", { status: 401 }));
    const err = (await postJson({ action: "get-me" }).catch((e) => e)) as ApiRequestError;
    expect(err.status).toBe(401);
    expect(err.message).toBe(SESSION_EXPIRED_MESSAGE);
  });

  it("passes through the backend error code and message", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(400, { error: { code: "bad_request", message: "입력이 올바르지 않습니다." } }),
    );
    const err = (await postJson({ action: "register" }).catch((e) => e)) as ApiRequestError;
    expect(err.status).toBe(400);
    expect(err.code).toBe("bad_request");
    expect(err.message).toBe("입력이 올바르지 않습니다.");
  });
});
