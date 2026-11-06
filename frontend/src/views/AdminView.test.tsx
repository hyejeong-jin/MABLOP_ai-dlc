import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AdminView } from "./AdminView";
import * as api from "../api";
import { ApiRequestError } from "../api";
import { formatKst } from "../format";

type Body = Record<string, unknown>;

interface User {
  userId: string;
  loginId: string;
  name: string;
  role: "admin" | "member";
  status: "pending" | "active" | "disabled";
  createdAt: string;
  approvedAt: string | null;
}

interface Grant {
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

interface State {
  users: User[];
  grants: Grant[];
}

type Handler = (body: Body, state: State) => unknown;

const PENDING_CREATED_AT = "2025-01-02T03:04:05.678Z";
const GRANT_REQUESTED_AT = "2025-03-04T05:06:07.000Z";

function user(over: Partial<User> & Pick<User, "userId" | "name" | "loginId">): User {
  return {
    role: "member",
    status: "active",
    createdAt: "2025-01-02T20:00:00.000Z", // 2025-01-03 in KST
    approvedAt: null,
    ...over,
  };
}

function defaultUsers(): User[] {
  return [
    user({ userId: "u_pend", name: "대기자", loginId: "pend01", status: "pending", createdAt: PENDING_CREATED_AT }),
    user({ userId: "u_act", name: "활동자", loginId: "act01", status: "active" }),
    user({ userId: "u_dis", name: "중지자", loginId: "dis01", status: "disabled" }),
    user({ userId: "u_adm", name: "관리자님", loginId: "admin01", role: "admin", status: "active" }),
  ];
}

function grant(over: Partial<Grant> & Pick<Grant, "userId" | "name" | "loginId" | "id">): Grant {
  return {
    status: "requested",
    purpose: null,
    requestedAt: GRANT_REQUESTED_AT,
    decidedAt: null,
    expiresAt: null,
    expired: false,
    ...over,
  };
}

function defaultGrants(): Grant[] {
  return [
    grant({
      userId: "u_req",
      name: "신청자",
      loginId: "req01",
      id: "g_req",
      status: "requested",
      purpose: "블로그 초안",
    }),
    grant({
      userId: "u_ok",
      name: "승인자",
      loginId: "ok01",
      id: "g_ok",
      status: "approved",
      expiresAt: "2025-01-31T16:00:00.000Z", // 2025-02-01 in KST
    }),
    grant({
      userId: "u_exp",
      name: "만료자",
      loginId: "exp01",
      id: "g_exp",
      status: "approved",
      expiresAt: "2024-12-31T00:00:00.000Z",
      expired: true,
    }),
    grant({ userId: "u_rej", name: "거절자", loginId: "rej01", id: "g_rej", status: "rejected" }),
    grant({ userId: "u_rev", name: "폐기자", loginId: "rev01", id: "g_rev", status: "revoked" }),
  ];
}

// Routes postJson by action over a small mutable server state and records every body.
// Unlisted actions resolve with {}. Handlers may throw to simulate a failed request.
function wireApi(opts: { users?: User[]; grants?: Grant[]; handlers?: Record<string, Handler> } = {}) {
  const state: State = {
    users: opts.users ?? defaultUsers(),
    grants: opts.grants ?? defaultGrants(),
  };
  const calls: Body[] = [];
  vi.spyOn(api, "postJson").mockImplementation((async (body: Body) => {
    calls.push({ ...body });
    const handler = opts.handlers?.[body.action as string];
    if (handler) return handler(body, state);
    switch (body.action) {
      case "admin-list-users":
        return { users: state.users.map((u) => ({ ...u })) };
      case "admin-grant-list":
        return { grants: state.grants.map((g) => ({ ...g })) };
      default:
        return {};
    }
  }) as never);
  return { calls, state };
}

function actions(calls: Body[], action: string): Body[] {
  return calls.filter((c) => c.action === action);
}

function setUserStatus(state: State, userId: unknown, status: User["status"]) {
  const target = state.users.find((u) => u.userId === userId);
  if (target) target.status = status;
}

function setGrantStatus(state: State, body: Body, status: Grant["status"], extra: Partial<Grant> = {}) {
  const target = state.grants.find((g) => g.userId === body.userId && g.id === body.grantId);
  if (target) Object.assign(target, { status, ...extra });
}

const region = (name: string) => screen.getByRole("region", { name });

function userRow(section: string, name: string): HTMLElement {
  return within(region(section)).getByText(name).closest(".user-row") as HTMLElement;
}

function grantRow(name: string): HTMLElement {
  return within(region("권한 관리")).getByText(name).closest(".grant-row") as HTMLElement;
}

function button(scope: HTMLElement, name: string): HTMLElement {
  return within(scope).getByRole("button", { name });
}

async function renderLoaded() {
  render(<AdminView />);
  await within(region("전체 사용자")).findByText("활동자");
  await within(region("권한 관리")).findByText("신청자");
}

function stubClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

test("requests admin-list-users and admin-grant-list once each on mount", async () => {
  const { calls } = wireApi();
  await renderLoaded();

  expect(actions(calls, "admin-list-users")).toEqual([{ action: "admin-list-users" }]);
  expect(actions(calls, "admin-grant-list")).toEqual([{ action: "admin-grant-list" }]);
  expect(screen.getByRole("heading", { name: "관리" })).toBeInTheDocument();
});

test("a failing user list does not hide the grant list", async () => {
  wireApi({
    handlers: {
      "admin-list-users": () => {
        throw new ApiRequestError(500, "internal", "사용자 목록 오류");
      },
    },
  });
  render(<AdminView />);

  expect(await within(region("가입 신청")).findByRole("alert")).toHaveTextContent("사용자 목록 오류");
  const grants = region("권한 관리");
  expect(await within(grants).findByText("신청자")).toBeInTheDocument();
  expect(within(grants).getByText("승인자")).toBeInTheDocument();
  expect(within(grants).queryByRole("alert")).toBeNull();
});

test("a failing grant list does not hide the user lists", async () => {
  wireApi({
    handlers: {
      "admin-grant-list": () => {
        throw new ApiRequestError(500, "internal", "권한 목록 오류");
      },
    },
  });
  render(<AdminView />);

  expect(await within(region("권한 관리")).findByRole("alert")).toHaveTextContent("권한 목록 오류");
  expect(await within(region("전체 사용자")).findByText("활동자")).toBeInTheDocument();
  expect(within(region("가입 신청")).getByText("대기자")).toBeInTheDocument();
  expect(within(region("가입 신청")).queryByRole("alert")).toBeNull();
  expect(within(region("전체 사용자")).queryByRole("alert")).toBeNull();
});

test("a load failure without a backend message shows the Korean fallback, not the raw error", async () => {
  wireApi({
    handlers: {
      "admin-list-users": () => {
        throw new TypeError("Failed to fetch");
      },
    },
  });
  render(<AdminView />);

  expect(await within(region("가입 신청")).findByRole("alert")).toHaveTextContent(
    "요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.",
  );
  expect(screen.queryByText(/Failed to fetch/)).toBeNull();
});

// ---------------------------------------------------------------------------
// 가입 신청
// ---------------------------------------------------------------------------

test("lists pending registrations with name, login id, request time and 승인/거절", async () => {
  wireApi();
  await renderLoaded();

  const section = region("가입 신청");
  expect(within(section).getAllByRole("button", { name: "승인" })).toHaveLength(1);
  const row = userRow("가입 신청", "대기자");
  expect(row).toHaveTextContent("아이디 pend01");
  expect(row).toHaveTextContent(`신청 ${formatKst(PENDING_CREATED_AT)}`);
  // 03:04 UTC is 12:04 in KST.
  expect(row).toHaveTextContent("12:04");
  expect(button(row, "승인")).toBeEnabled();
  expect(button(row, "거절")).toBeEnabled();
  // Only pending users are listed here.
  expect(within(section).queryByText("활동자")).toBeNull();
});

test("shows the empty message when no registration is pending", async () => {
  wireApi({ users: defaultUsers().filter((u) => u.status !== "pending") });
  render(<AdminView />);

  expect(await within(region("가입 신청")).findByText("대기 중인 가입 신청이 없습니다.")).toBeInTheDocument();
  expect(within(region("가입 신청")).queryByRole("button")).toBeNull();
});

test("승인 sends admin-approve-user and reloads the user list", async () => {
  const { calls } = wireApi({
    handlers: {
      "admin-approve-user": (body, state) => {
        setUserStatus(state, body.userId, "active");
        return { userId: body.userId, status: "active" };
      },
    },
  });
  await renderLoaded();

  fireEvent.click(button(userRow("가입 신청", "대기자"), "승인"));

  expect(await within(region("가입 신청")).findByText("대기 중인 가입 신청이 없습니다.")).toBeInTheDocument();
  expect(actions(calls, "admin-approve-user")).toEqual([{ action: "admin-approve-user", userId: "u_pend" }]);
  expect(actions(calls, "admin-list-users")).toHaveLength(2);
  expect(within(userRow("전체 사용자", "대기자")).getByText("활성")).toBeInTheDocument();
  // No legacy notice for a plain approval.
  expect(screen.queryByRole("alert")).toBeNull();
});

test("거절 asks for an inline 확인/취소 first and sends only after 확인", async () => {
  const { calls } = wireApi({
    handlers: {
      "admin-reject-user": (body, state) => {
        state.users = state.users.filter((u) => u.userId !== body.userId);
        return { userId: body.userId };
      },
    },
  });
  await renderLoaded();

  const row = userRow("가입 신청", "대기자");
  fireEvent.click(button(row, "거절"));
  expect(within(row).getByText("이 가입 신청을 거절할까요?")).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: "승인" })).toBeNull();
  expect(actions(calls, "admin-reject-user")).toHaveLength(0);

  // 취소 restores the buttons and sends nothing.
  fireEvent.click(button(row, "취소"));
  expect(within(row).queryByText("이 가입 신청을 거절할까요?")).toBeNull();
  expect(button(row, "승인")).toBeInTheDocument();
  expect(actions(calls, "admin-reject-user")).toHaveLength(0);

  fireEvent.click(button(row, "거절"));
  fireEvent.click(button(row, "확인"));

  expect(await within(region("가입 신청")).findByText("대기 중인 가입 신청이 없습니다.")).toBeInTheDocument();
  expect(actions(calls, "admin-reject-user")).toEqual([{ action: "admin-reject-user", userId: "u_pend" }]);
  expect(actions(calls, "admin-list-users")).toHaveLength(2);
});

test("a failed 승인 shows the backend message next to the pending user only", async () => {
  wireApi({
    handlers: {
      "admin-approve-user": () => {
        throw new ApiRequestError(409, "conflict", "이미 처리된 가입 신청입니다.");
      },
    },
  });
  await renderLoaded();

  const row = userRow("가입 신청", "대기자");
  fireEvent.click(button(row, "승인"));

  expect(await within(row).findByRole("alert")).toHaveTextContent("이미 처리된 가입 신청입니다.");
  // The row stays actionable and nothing is duplicated in the all-users list.
  expect(button(row, "승인")).toBeEnabled();
  expect(within(region("전체 사용자")).queryByRole("alert")).toBeNull();
});

test("a second click on 승인 while the request is running sends nothing", async () => {
  let release: (value: unknown) => void = () => {};
  const { calls } = wireApi({
    handlers: {
      "admin-approve-user": (body, state) =>
        new Promise((resolve) => {
          release = () => {
            setUserStatus(state, body.userId, "active");
            resolve({ userId: body.userId, status: "active" });
          };
        }),
    },
  });
  await renderLoaded();

  const row = userRow("가입 신청", "대기자");
  const approve = button(row, "승인");
  fireEvent.click(approve);
  await waitFor(() => expect(approve).toBeDisabled());
  expect(button(row, "거절")).toBeDisabled();
  fireEvent.click(approve);

  expect(actions(calls, "admin-approve-user")).toHaveLength(1);
  release(undefined);
  expect(await within(region("가입 신청")).findByText("대기 중인 가입 신청이 없습니다.")).toBeInTheDocument();
  expect(actions(calls, "admin-approve-user")).toHaveLength(1);
});

// ---------------------------------------------------------------------------
// 전체 사용자
// ---------------------------------------------------------------------------

test("lists every user with name, login id, status badge and KST join date", async () => {
  wireApi();
  await renderLoaded();

  const act = userRow("전체 사용자", "활동자");
  expect(act).toHaveTextContent("아이디 act01");
  // 2025-01-02T20:00Z is already 2025-01-03 in KST.
  expect(act).toHaveTextContent("가입일 2025-01-03");
  expect(within(act).getByText("활성")).toHaveClass("badge-active");

  expect(within(userRow("전체 사용자", "대기자")).getByText("대기")).toHaveClass("badge-pending");
  expect(within(userRow("전체 사용자", "중지자")).getByText("비활성")).toHaveClass("badge-disabled");
  expect(userRow("전체 사용자", "관리자님")).toHaveTextContent("아이디 admin01");
});

test("shows 비활성화 for active members, 재활성화 for disabled members and nothing for admins", async () => {
  wireApi();
  await renderLoaded();

  const act = userRow("전체 사용자", "활동자");
  expect(button(act, "비활성화")).toBeInTheDocument();
  expect(button(act, "비밀번호 초기화")).toBeInTheDocument();
  expect(within(act).queryByRole("button", { name: "재활성화" })).toBeNull();

  const dis = userRow("전체 사용자", "중지자");
  expect(button(dis, "재활성화")).toBeInTheDocument();
  expect(button(dis, "비밀번호 초기화")).toBeInTheDocument();
  expect(within(dis).queryByRole("button", { name: "비활성화" })).toBeNull();

  const admin = userRow("전체 사용자", "관리자님");
  expect(within(admin).queryByRole("button")).toBeNull();
});

test("비활성화 asks for inline 확인/취소, then sends and reloads with the new status", async () => {
  const { calls } = wireApi({
    handlers: {
      "admin-disable-user": (body, state) => {
        setUserStatus(state, body.userId, "disabled");
        return { userId: body.userId, status: "disabled" };
      },
    },
  });
  await renderLoaded();

  const row = userRow("전체 사용자", "활동자");
  fireEvent.click(button(row, "비활성화"));
  expect(within(row).getByText("이 계정을 비활성화할까요?")).toBeInTheDocument();
  expect(actions(calls, "admin-disable-user")).toHaveLength(0);

  fireEvent.click(button(row, "취소"));
  expect(within(row).queryByText("이 계정을 비활성화할까요?")).toBeNull();
  expect(button(row, "비활성화")).toBeInTheDocument();
  expect(actions(calls, "admin-disable-user")).toHaveLength(0);

  fireEvent.click(button(row, "비활성화"));
  fireEvent.click(button(row, "확인"));

  await waitFor(() => expect(within(userRow("전체 사용자", "활동자")).getByText("비활성")).toBeInTheDocument());
  expect(actions(calls, "admin-disable-user")).toEqual([{ action: "admin-disable-user", userId: "u_act" }]);
  expect(actions(calls, "admin-list-users")).toHaveLength(2);
  expect(button(userRow("전체 사용자", "활동자"), "재활성화")).toBeInTheDocument();
});

test("재활성화 sends immediately without a confirmation and reloads", async () => {
  const { calls } = wireApi({
    handlers: {
      "admin-enable-user": (body, state) => {
        setUserStatus(state, body.userId, "active");
        return { userId: body.userId, status: "active" };
      },
    },
  });
  await renderLoaded();

  fireEvent.click(button(userRow("전체 사용자", "중지자"), "재활성화"));

  await waitFor(() => expect(within(userRow("전체 사용자", "중지자")).getByText("활성")).toBeInTheDocument());
  expect(actions(calls, "admin-enable-user")).toEqual([{ action: "admin-enable-user", userId: "u_dis" }]);
  expect(actions(calls, "admin-list-users")).toHaveLength(2);
});

test("계정 삭제 needs 확인, then calls admin-delete-user and reloads the list", async () => {
  const { calls } = wireApi({
    handlers: {
      "admin-delete-user": (body, state) => {
        state.users = state.users.filter((u) => u.userId !== body.userId);
        return { userId: body.userId, deleted: true };
      },
    },
  });
  await renderLoaded();

  const row = userRow("전체 사용자", "활동자");
  fireEvent.click(button(row, "계정 삭제"));
  expect(
    within(row).getByText("정말 삭제할까요? 이 사용자의 모든 데이터가 영구 삭제됩니다."),
  ).toBeInTheDocument();
  expect(actions(calls, "admin-delete-user")).toHaveLength(0);

  fireEvent.click(button(row, "취소"));
  expect(actions(calls, "admin-delete-user")).toHaveLength(0);

  fireEvent.click(button(row, "계정 삭제"));
  fireEvent.click(button(row, "확인"));

  await waitFor(() => expect(actions(calls, "admin-delete-user")).toEqual([{ action: "admin-delete-user", userId: "u_act" }]));
  await waitFor(() => expect(actions(calls, "admin-list-users")).toHaveLength(2));
  await waitFor(() => expect(screen.queryByText("활동자")).toBeNull());
});

test("비밀번호 초기화 needs 확인, then shows the temporary password with copy, notice and 닫기", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  stubClipboard(writeText);
  const { calls } = wireApi({
    handlers: {
      "admin-reset-password": (body) => ({ userId: body.userId, temporaryPassword: "Tmp-Pass-1234" }),
    },
  });
  await renderLoaded();

  const row = userRow("전체 사용자", "활동자");
  fireEvent.click(button(row, "비밀번호 초기화"));
  expect(within(row).getByText("비밀번호를 초기화할까요?")).toBeInTheDocument();
  expect(actions(calls, "admin-reset-password")).toHaveLength(0);

  fireEvent.click(button(row, "취소"));
  expect(actions(calls, "admin-reset-password")).toHaveLength(0);
  expect(screen.queryByText("Tmp-Pass-1234")).toBeNull();

  fireEvent.click(button(row, "비밀번호 초기화"));
  fireEvent.click(button(row, "확인"));

  const box = await screen.findByRole("group", { name: "임시 비밀번호" });
  expect(actions(calls, "admin-reset-password")).toEqual([{ action: "admin-reset-password", userId: "u_act" }]);
  expect(within(box).getByText("Tmp-Pass-1234")).toBeInTheDocument();
  expect(box).toHaveTextContent("활동자(act01)");
  expect(within(box).getByText("이 비밀번호는 다시 표시되지 않습니다.")).toBeInTheDocument();
  // The list is reloaded after the successful reset.
  expect(actions(calls, "admin-list-users")).toHaveLength(2);

  // The password is held in component state only.
  expect(JSON.stringify({ ...localStorage })).not.toContain("Tmp-Pass-1234");
  expect(JSON.stringify({ ...sessionStorage })).not.toContain("Tmp-Pass-1234");

  fireEvent.click(button(box, "복사"));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("Tmp-Pass-1234"));
  expect(await within(box).findByText("복사됨")).toBeInTheDocument();

  fireEvent.click(button(box, "닫기"));
  expect(screen.queryByText("Tmp-Pass-1234")).toBeNull();
  expect(screen.queryByRole("group", { name: "임시 비밀번호" })).toBeNull();
});

test("a failed clipboard copy tells the administrator to copy the password by hand", async () => {
  stubClipboard(vi.fn().mockRejectedValue(new Error("denied")));
  wireApi({
    handlers: {
      "admin-reset-password": (body) => ({ userId: body.userId, temporaryPassword: "Tmp-Pass-5678" }),
    },
  });
  await renderLoaded();

  const row = userRow("전체 사용자", "활동자");
  fireEvent.click(button(row, "비밀번호 초기화"));
  fireEvent.click(button(row, "확인"));

  const box = await screen.findByRole("group", { name: "임시 비밀번호" });
  fireEvent.click(button(box, "복사"));

  expect(await within(box).findByRole("alert")).toHaveTextContent("복사하지 못했습니다.");
  expect(within(box).getByText("Tmp-Pass-5678")).toBeInTheDocument();
});

test("a failed 비밀번호 초기화 shows the backend message next to that user and no password", async () => {
  wireApi({
    handlers: {
      "admin-reset-password": () => {
        throw new ApiRequestError(404, "not_found", "사용자를 찾을 수 없습니다.");
      },
    },
  });
  await renderLoaded();

  const row = userRow("전체 사용자", "활동자");
  fireEvent.click(button(row, "비밀번호 초기화"));
  fireEvent.click(button(row, "확인"));

  expect(await within(row).findByRole("alert")).toHaveTextContent("사용자를 찾을 수 없습니다.");
  expect(screen.queryByRole("group", { name: "임시 비밀번호" })).toBeNull();
  expect(within(userRow("전체 사용자", "중지자")).queryByRole("alert")).toBeNull();
  expect(within(userRow("전체 사용자", "관리자님")).queryByRole("alert")).toBeNull();
});

test("a failed 비활성화 without a backend message shows the Korean fallback next to that user", async () => {
  wireApi({
    handlers: {
      "admin-disable-user": () => {
        throw new TypeError("Failed to fetch");
      },
    },
  });
  await renderLoaded();

  const row = userRow("전체 사용자", "활동자");
  fireEvent.click(button(row, "비활성화"));
  fireEvent.click(button(row, "확인"));

  expect(await within(row).findByRole("alert")).toHaveTextContent(
    "요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.",
  );
  expect(screen.queryByText(/Failed to fetch/)).toBeNull();
  // The row shows its status unchanged and stays actionable.
  expect(within(row).getByText("활성")).toBeInTheDocument();
  expect(button(row, "비활성화")).toBeEnabled();
});

// ---------------------------------------------------------------------------
// Legacy binding notices
// ---------------------------------------------------------------------------

test.each([
  ["failed", "기존 데이터 연결에 실패했습니다. 관리자에게 문의하세요."],
  ["already_bound", "이미 다른 계정에 연결된 기존 데이터입니다."],
])("approving with legacyBinding %s shows the notice and keeps the user active", async (status, message) => {
  wireApi({
    handlers: {
      "admin-approve-user": (body, state) => {
        setUserStatus(state, body.userId, "active");
        return { userId: body.userId, status: "active", legacyBinding: { status, message } };
      },
    },
  });
  await renderLoaded();

  fireEvent.click(button(userRow("가입 신청", "대기자"), "승인"));

  const row = userRow("전체 사용자", "대기자");
  expect(await within(row).findByRole("alert")).toHaveTextContent(message);
  expect(within(row).getByText("활성")).toBeInTheDocument();
  // The approval itself stays in effect: the pending list is empty.
  expect(within(region("가입 신청")).getByText("대기 중인 가입 신청이 없습니다.")).toBeInTheDocument();
});

test("a failed legacyBinding without a message falls back to a Korean notice", async () => {
  wireApi({
    handlers: {
      "admin-approve-user": (body, state) => {
        setUserStatus(state, body.userId, "active");
        return { userId: body.userId, status: "active", legacyBinding: { status: "failed", message: null } };
      },
    },
  });
  await renderLoaded();

  fireEvent.click(button(userRow("가입 신청", "대기자"), "승인"));

  expect(await within(userRow("전체 사용자", "대기자")).findByRole("alert")).toHaveTextContent(
    "기존 데이터 연결에 실패했습니다. 승인은 유지되었습니다.",
  );
});

test.each(["succeeded", "not_applicable"])("approving with legacyBinding %s shows no notice", async (status) => {
  const { calls } = wireApi({
    handlers: {
      "admin-approve-user": (body, state) => {
        setUserStatus(state, body.userId, "active");
        return {
          userId: body.userId,
          status: "active",
          legacyBinding: { status, message: "표시되면 안 되는 문구" },
        };
      },
    },
  });
  await renderLoaded();

  fireEvent.click(button(userRow("가입 신청", "대기자"), "승인"));

  await waitFor(() => expect(actions(calls, "admin-list-users")).toHaveLength(2));
  expect(await within(region("가입 신청")).findByText("대기 중인 가입 신청이 없습니다.")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText("표시되면 안 되는 문구")).toBeNull();
});

// ---------------------------------------------------------------------------
// 권한 관리
// ---------------------------------------------------------------------------

test("lists requested grants with name, login id, purpose, request time, expiry selector, 승인 and 거절", async () => {
  wireApi();
  await renderLoaded();

  const row = grantRow("신청자");
  expect(row).toHaveTextContent("아이디 req01");
  expect(row).toHaveTextContent("사용 목적 블로그 초안");
  expect(row).toHaveTextContent(`신청 ${formatKst(GRANT_REQUESTED_AT)}`);
  expect(button(row, "승인")).toBeEnabled();
  expect(button(row, "거절")).toBeEnabled();

  const select = within(row).getByLabelText("만료 기간") as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["7일", "30일", "90일"]);
  expect(select.value).toBe("30");
  // Only requested grants are in this list.
  expect(within(row).queryByText("승인자")).toBeNull();
});

test("승인 sends expiresInDays 30 by default and reloads the grant list", async () => {
  const { calls } = wireApi({
    handlers: {
      "admin-grant-approve": (body, state) => {
        setGrantStatus(state, body, "approved", { expiresAt: "2025-04-03T05:06:07.000Z" });
        return {};
      },
    },
  });
  await renderLoaded();

  fireEvent.click(button(grantRow("신청자"), "승인"));

  await waitFor(() => expect(within(region("권한 관리")).getByText("대기 중인 사용 권한 신청이 없습니다.")).toBeInTheDocument());
  expect(actions(calls, "admin-grant-approve")).toEqual([
    { action: "admin-grant-approve", userId: "u_req", grantId: "g_req", expiresInDays: 30 },
  ]);
  expect(actions(calls, "admin-grant-list")).toHaveLength(2);
  // The approved grant moved to the issued list with an approval badge.
  expect(within(grantRow("신청자")).getByText("승인")).toHaveClass("badge-active");
});

test.each([7, 90])("승인 sends expiresInDays %i after choosing it in the selector", async (days) => {
  const { calls } = wireApi({
    handlers: {
      "admin-grant-approve": (body, state) => {
        setGrantStatus(state, body, "approved");
        return {};
      },
    },
  });
  await renderLoaded();

  const row = grantRow("신청자");
  fireEvent.change(within(row).getByLabelText("만료 기간"), { target: { value: String(days) } });
  expect((within(row).getByLabelText("만료 기간") as HTMLSelectElement).value).toBe(String(days));
  fireEvent.click(button(row, "승인"));

  await waitFor(() => expect(actions(calls, "admin-grant-approve")).toHaveLength(1));
  expect(actions(calls, "admin-grant-approve")[0]).toEqual({
    action: "admin-grant-approve",
    userId: "u_req",
    grantId: "g_req",
    expiresInDays: days,
  });
});

test("the 거절 for a grant asks for inline 확인/취소 and sends no expiresInDays", async () => {
  const { calls } = wireApi({
    handlers: {
      "admin-grant-reject": (body, state) => {
        setGrantStatus(state, body, "rejected");
        return {};
      },
    },
  });
  await renderLoaded();

  const row = grantRow("신청자");
  fireEvent.click(button(row, "거절"));
  expect(within(row).getByText("이 사용 권한 신청을 거절할까요?")).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: "승인" })).toBeNull();
  expect(actions(calls, "admin-grant-reject")).toHaveLength(0);

  fireEvent.click(button(row, "취소"));
  expect(within(row).queryByText("이 사용 권한 신청을 거절할까요?")).toBeNull();
  expect(button(row, "승인")).toBeInTheDocument();
  expect(actions(calls, "admin-grant-reject")).toHaveLength(0);

  fireEvent.click(button(row, "거절"));
  fireEvent.click(button(row, "확인"));

  await waitFor(() => expect(within(region("권한 관리")).getByText("대기 중인 사용 권한 신청이 없습니다.")).toBeInTheDocument());
  expect(actions(calls, "admin-grant-reject")).toEqual([
    { action: "admin-grant-reject", userId: "u_req", grantId: "g_req" },
  ]);
  expect(actions(calls, "admin-grant-list")).toHaveLength(2);
  expect(within(grantRow("신청자")).getByText("거절")).toBeInTheDocument();
});

test("shows the empty message when no grant is requested", async () => {
  wireApi({ grants: defaultGrants().filter((g) => g.status !== "requested") });
  render(<AdminView />);

  expect(
    await within(region("권한 관리")).findByText("대기 중인 사용 권한 신청이 없습니다."),
  ).toBeInTheDocument();
  expect(within(region("권한 관리")).getByText("승인자")).toBeInTheDocument();
});

test("issued grants show the status badge and KST expiry date", async () => {
  wireApi();
  await renderLoaded();

  const ok = grantRow("승인자");
  expect(ok).toHaveTextContent("아이디 ok01");
  expect(within(ok).getByText("승인")).toHaveClass("badge-active");
  // 2025-01-31T16:00Z is already 2025-02-01 in KST.
  expect(ok).toHaveTextContent("만료일 2025-02-01");

  const expired = grantRow("만료자");
  expect(within(expired).getByText("만료")).toHaveClass("badge-disabled");
  expect(expired).toHaveTextContent("만료일 2024-12-31");

  expect(within(grantRow("거절자")).getByText("거절")).toBeInTheDocument();
  expect(within(grantRow("폐기자")).getByText("폐기")).toBeInTheDocument();
  expect(grantRow("거절자")).toHaveTextContent("만료일 -");
});

test("폐기 appears only for approved grants that have not expired", async () => {
  wireApi();
  await renderLoaded();

  expect(button(grantRow("승인자"), "폐기")).toBeInTheDocument();
  expect(within(grantRow("만료자")).queryByRole("button")).toBeNull();
  expect(within(grantRow("거절자")).queryByRole("button")).toBeNull();
  expect(within(grantRow("폐기자")).queryByRole("button")).toBeNull();
  expect(within(region("권한 관리")).getAllByRole("button", { name: "폐기" })).toHaveLength(1);
});

test("폐기 asks for inline 확인/취소, then sends admin-grant-revoke and reloads", async () => {
  const { calls } = wireApi({
    handlers: {
      "admin-grant-revoke": (body, state) => {
        setGrantStatus(state, body, "revoked");
        return {};
      },
    },
  });
  await renderLoaded();

  const row = grantRow("승인자");
  fireEvent.click(button(row, "폐기"));
  expect(within(row).getByText("이 사용 권한을 폐기할까요?")).toBeInTheDocument();
  expect(actions(calls, "admin-grant-revoke")).toHaveLength(0);

  fireEvent.click(button(row, "취소"));
  expect(within(row).queryByText("이 사용 권한을 폐기할까요?")).toBeNull();
  expect(button(row, "폐기")).toBeInTheDocument();
  expect(actions(calls, "admin-grant-revoke")).toHaveLength(0);

  fireEvent.click(button(row, "폐기"));
  fireEvent.click(button(row, "확인"));

  await waitFor(() => expect(within(grantRow("승인자")).queryByRole("button", { name: "폐기" })).toBeNull());
  expect(actions(calls, "admin-grant-revoke")).toEqual([
    { action: "admin-grant-revoke", userId: "u_ok", grantId: "g_ok" },
  ]);
  expect(actions(calls, "admin-grant-list")).toHaveLength(2);
  // Two rows now carry the 폐기 badge (the previously revoked one and this one).
  expect(within(grantRow("승인자")).getByText("폐기")).toHaveClass("badge");
});

test("shows the issued-grants empty message when nothing has been issued", async () => {
  wireApi({ grants: defaultGrants().filter((g) => g.status === "requested") });
  render(<AdminView />);

  expect(await within(region("권한 관리")).findByText("발급된 사용 권한이 없습니다.")).toBeInTheDocument();
});

test("a failed grant action shows the backend message next to that grant only", async () => {
  wireApi({
    handlers: {
      "admin-grant-approve": () => {
        throw new ApiRequestError(409, "conflict", "이미 처리된 신청입니다.");
      },
      "admin-grant-revoke": () => {
        throw new ApiRequestError(409, "conflict", "이미 만료된 사용 권한입니다.");
      },
    },
  });
  await renderLoaded();

  const requested = grantRow("신청자");
  fireEvent.click(button(requested, "승인"));
  expect(await within(requested).findByRole("alert")).toHaveTextContent("이미 처리된 신청입니다.");
  expect(button(requested, "승인")).toBeEnabled();
  expect(within(grantRow("승인자")).queryByRole("alert")).toBeNull();

  const issued = grantRow("승인자");
  fireEvent.click(button(issued, "폐기"));
  fireEvent.click(button(issued, "확인"));
  expect(await within(issued).findByRole("alert")).toHaveTextContent("이미 만료된 사용 권한입니다.");
  expect(within(requested).getByRole("alert")).toHaveTextContent("이미 처리된 신청입니다.");
  expect(within(region("전체 사용자")).queryByRole("alert")).toBeNull();
});

test("a second click on a grant 승인 while the request is running sends nothing", async () => {
  let release: () => void = () => {};
  const { calls } = wireApi({
    handlers: {
      "admin-grant-approve": (body, state) =>
        new Promise((resolve) => {
          release = () => {
            setGrantStatus(state, body, "approved");
            resolve({});
          };
        }),
    },
  });
  await renderLoaded();

  const row = grantRow("신청자");
  const approve = button(row, "승인");
  fireEvent.click(approve);
  await waitFor(() => expect(approve).toBeDisabled());
  expect(button(row, "거절")).toBeDisabled();
  expect(within(row).getByLabelText("만료 기간")).toBeDisabled();
  fireEvent.click(approve);

  expect(actions(calls, "admin-grant-approve")).toHaveLength(1);
  release();
  await waitFor(() => expect(actions(calls, "admin-grant-list")).toHaveLength(2));
  expect(actions(calls, "admin-grant-approve")).toHaveLength(1);
});
