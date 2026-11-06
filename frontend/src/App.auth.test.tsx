// App-level authentication flow (task 14.10).
// fetch is stubbed (not postJson), so every request goes through the real api.ts,
// AuthContext and session.ts. Requirements: 10.3, 13.1, 13.6, 13.7, 13.8, 14.6,
// 15.1, 15.2, 23.5, 23.11.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { SESSION_KEY } from "./auth/session";

type Role = "admin" | "member";
type Body = Record<string, unknown>;
type Reply = { status?: number; data: unknown };

interface Call {
  body: Body;
  headers: Record<string, string>;
}

const SESSION_EXPIRED = "로그인이 만료되었습니다. 다시 로그인하세요.";
const FUTURE = () => new Date(Date.now() + 3600_000).toISOString();

let calls: Call[] = [];
// Per-test overrides. Return undefined to fall back to the default reply.
let override: (body: Body) => Reply | undefined = () => undefined;

function defaultReply(body: Body): Reply {
  switch (body.action) {
    case "get-learning-status":
      return { data: { processedPosts: 0, chunks: 0, posts: [] } };
    case "get-me":
      return { data: { loginId: "user1", name: "사용자" } };
    case "grant-list-mine":
      return { data: { grants: [] } };
    case "list-drafts":
      return { data: { drafts: [] } };
    default:
      return { data: {} };
  }
}

function callsOf(action: string): Call[] {
  return calls.filter((c) => c.body.action === action);
}

beforeEach(() => {
  calls = [];
  override = () => undefined;
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: { headers: Record<string, string>; body: string }) => {
      const body = JSON.parse(init.body) as Body;
      calls.push({ body, headers: init.headers });
      const reply = override(body) ?? defaultReply(body);
      const status = reply.status ?? 200;
      return { ok: status >= 200 && status < 300, status, json: async () => reply.data };
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function seedSession(
  role: Role,
  extra: { token?: string; expiresAt?: string; loginId?: string } = {},
) {
  localStorage.setItem(
    SESSION_KEY,
    JSON.stringify({
      token: extra.token ?? "t",
      expiresAt: extra.expiresAt ?? FUTURE(),
      user: { loginId: extra.loginId ?? "user1", name: "사용자", role },
    }),
  );
}

function storedSession() {
  const raw = localStorage.getItem(SESSION_KEY);
  return raw === null ? null : JSON.parse(raw);
}

function openTab(name: string) {
  fireEvent.click(screen.getByRole("button", { name }));
}

test("login gate: no session shows only the login screen, then login stores the session and shows the tabs", async () => {
  override = (body) =>
    body.action === "login"
      ? {
          data: {
            token: "tok-1",
            expiresAt: "2999-01-01T00:00:00.000Z",
            user: { loginId: "user1", name: "사용자", role: "member" },
          },
        }
      : undefined;

  render(<App />);
  expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "주요 기능" })).not.toBeInTheDocument();
  expect(localStorage.getItem(SESSION_KEY)).toBeNull();

  fireEvent.change(screen.getByLabelText("아이디"), { target: { value: "user1" } });
  fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "correct-horse-1" } });
  fireEvent.click(screen.getByRole("button", { name: "로그인" }));

  expect(await screen.findByRole("navigation", { name: "주요 기능" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "스타일 학습" })).toBeInTheDocument();
  expect(storedSession()).toEqual({
    token: "tok-1",
    expiresAt: "2999-01-01T00:00:00.000Z",
    user: { loginId: "user1", name: "사용자", role: "member" },
  });

  // The login request is public (no token); later requests carry the new token.
  const login = callsOf("login")[0];
  expect(login.body).toMatchObject({ loginId: "user1", password: "correct-horse-1" });
  expect(login.headers["X-Mablop-Token"]).toBeUndefined();
  await waitFor(() => expect(callsOf("get-learning-status").length).toBeGreaterThan(0));
  expect(callsOf("get-learning-status")[0].headers["X-Mablop-Token"]).toBe("tok-1");
});

test("an expired stored session is deleted at startup and the login screen is shown", () => {
  seedSession("member", { expiresAt: new Date(Date.now() - 1000).toISOString() });

  render(<App />);

  expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "주요 기능" })).not.toBeInTheDocument();
  expect(calls).toHaveLength(0);
});

test("a 401 on an authenticated request clears the session and shows the expiry notice on the login screen", async () => {
  seedSession("member", { token: "stale" });
  override = (body) =>
    body.action === "get-me"
      ? { status: 401, data: { error: { code: "unauthorized", message: "invalid token" } } }
      : undefined;

  render(<App />);
  openTab("내 정보");

  expect(await screen.findByText(SESSION_EXPIRED)).toBeInTheDocument();
  expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "주요 기능" })).not.toBeInTheDocument();
  expect(callsOf("get-me")[0].headers["X-Mablop-Token"]).toBe("stale");
});

test("logout from 내 정보 clears the session and returns to the login screen", async () => {
  seedSession("member");
  render(<App />);
  openTab("내 정보");
  expect(await screen.findByRole("heading", { name: "내 정보" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "로그아웃" }));

  expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "주요 기능" })).not.toBeInTheDocument();
  expect(screen.queryByText(SESSION_EXPIRED)).not.toBeInTheDocument();
});

test("startup removes legacy keys and keeps the session and grant selection", () => {
  seedSession("member");
  localStorage.setItem("mablop-token", "old-shared-token");
  localStorage.setItem("mablop-last-draft", "x");
  localStorage.setItem("mablop-last-draft:abc", "y");
  localStorage.setItem("mablop-grant-id:user1", "g1");
  localStorage.setItem("mablop-grant-id:other", "g2");

  render(<App />);

  expect(localStorage.getItem("mablop-token")).toBeNull();
  expect(localStorage.getItem("mablop-last-draft")).toBeNull();
  expect(localStorage.getItem("mablop-last-draft:abc")).toBeNull();
  expect(localStorage.getItem(SESSION_KEY)).not.toBeNull();
  expect(localStorage.getItem("mablop-grant-id:user1")).toBe("g1");
  expect(localStorage.getItem("mablop-grant-id:other")).toBe("g2");
  expect(screen.getByRole("navigation", { name: "주요 기능" })).toBeInTheDocument();
});

test("legacy keys are removed even when there is no session", () => {
  localStorage.setItem("mablop-token", "old-shared-token");
  localStorage.setItem("mablop-last-draft:abc", "y");

  render(<App />);

  expect(localStorage.getItem("mablop-token")).toBeNull();
  expect(localStorage.getItem("mablop-last-draft:abc")).toBeNull();
  expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
});

test("the 관리 tab is shown to admins and hidden from members", () => {
  seedSession("member");
  const { unmount } = render(<App />);
  expect(screen.getByRole("button", { name: "내 정보" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "관리" })).not.toBeInTheDocument();
  unmount();

  seedSession("admin");
  render(<App />);
  expect(screen.getByRole("button", { name: "관리" })).toBeInTheDocument();
});

test("a draftId from 초안 작성 is handed to 결과 once and then cleared", async () => {
  seedSession("admin"); // admins need no Grant, so the form can be submitted directly
  const drafts = [
    { draftId: "d-new", title: "새 글", createdAt: "2025-01-02T00:00:00.000Z" },
    { draftId: "d-old", title: "옛 글", createdAt: "2025-01-01T00:00:00.000Z" },
  ];
  override = (body) => {
    if (body.action === "generate-draft") return { data: { draftId: "d-new", markdown: "# 새 글" } };
    if (body.action === "list-drafts") return { data: { drafts } };
    if (body.action === "get-result") return { data: { markdown: `본문 ${String(body.id)}` } };
    return undefined;
  };

  render(<App />);
  openTab("초안 작성");
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "새 글" } });
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  expect(await screen.findByLabelText("초안 결과")).toBeInTheDocument();
  expect(callsOf("generate-draft")[0].body.grantId).toBeUndefined();

  // Entering 결과 selects the new draft and opens its body.
  openTab("결과");
  expect(await screen.findByLabelText("생성된 마크다운")).toHaveTextContent("본문 d-new");
  expect(callsOf("get-result")).toHaveLength(1);
  expect(callsOf("get-result")[0].body).toMatchObject({ kind: "draft", id: "d-new" });

  // Leaving and re-entering does not select it again (the hand-off was consumed).
  openTab("내 정보");
  openTab("결과");
  expect(await screen.findByText("왼쪽 목록에서 초안을 선택하세요.")).toBeInTheDocument();
  expect(callsOf("list-drafts")).toHaveLength(2);
  expect(callsOf("get-result")).toHaveLength(1);
});

test("changing the password replaces only token and expiresAt in the stored session", async () => {
  const oldExpiry = FUTURE();
  const newExpiry = "2999-06-01T00:00:00.000Z";
  seedSession("member", { token: "old-token", expiresAt: oldExpiry });
  override = (body) =>
    body.action === "change-password"
      ? { data: { token: "new-token", expiresAt: newExpiry } }
      : undefined;

  render(<App />);
  openTab("내 정보");
  fireEvent.change(await screen.findByLabelText("현재 비밀번호"), { target: { value: "old-pass-1234" } });
  fireEvent.change(screen.getByLabelText("새 비밀번호"), { target: { value: "new-pass-5678" } });
  fireEvent.change(screen.getByLabelText("새 비밀번호 확인"), { target: { value: "new-pass-5678" } });
  fireEvent.click(screen.getByRole("button", { name: "비밀번호 변경" }));

  expect(await screen.findByText("비밀번호가 변경되었습니다.")).toBeInTheDocument();
  expect(storedSession()).toEqual({
    token: "new-token",
    expiresAt: newExpiry,
    user: { loginId: "user1", name: "사용자", role: "member" },
  });
  expect(callsOf("change-password")[0].headers["X-Mablop-Token"]).toBe("old-token");
  // Still logged in.
  expect(screen.getByRole("navigation", { name: "주요 기능" })).toBeInTheDocument();
});
